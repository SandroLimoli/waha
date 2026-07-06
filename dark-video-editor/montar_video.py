#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
montar_video.py — Editor automático de vídeos dark para YouTube.

Recebe uma pasta de projeto com narração (TTS), legendas (SRT), trilha
musical e imagens numeradas, e entrega o vídeo final renderizado pelo
FFmpeg, pronto para upload.

Uso:
    python montar_video.py videos/2026-07-10-titulo-do-video/
    python montar_video.py videos/2026-07-10-titulo-do-video/ --vertical

Saída:
    <pasta>/final.mp4            (horizontal 1920x1080)
    <pasta>/final_vertical.mp4   (com --vertical, 1080x1920)

Dependências: apenas Python 3.8+ e FFmpeg/FFprobe no PATH.
Nenhum arquivo original da pasta do vídeo é modificado ou apagado —
arquivos intermediários (legenda ASS, SRT deslocado) vão para uma
pasta temporária do sistema.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

#
# Constantes e códigos de saída
#

# Extensões aceitas para os arquivos de cada tipo
EXTENSOES_AUDIO = ('.mp3', '.wav')
EXTENSOES_IMAGEM = ('.png', '.jpg', '.jpeg')

# Tolerância ao verificar se uma imagem é 16:9 (2%)
TOLERANCIA_PROPORCAO = 0.02

# Duração da tela preta no início e no fim (segundos)
TELA_PRETA_S = 1.5

# Ciclo de movimentos Ken Burns aplicado às imagens, nesta ordem
MOVIMENTOS_KENBURNS = ('zoom-in', 'pan-esquerda', 'zoom-out', 'pan-direita')


def erro(mensagem, etapa=None):
    """Imprime uma mensagem de erro clara em português e encerra."""
    prefixo = f'❌ [{etapa}] ' if etapa else '❌ '
    print(prefixo + mensagem, file=sys.stderr)
    sys.exit(1)


def etapa_ok(mensagem):
    """Imprime o marcador de etapa concluída."""
    print('✓ ' + mensagem)


def formatar_duracao(segundos):
    """Formata segundos como 8m42s (ou 42s se menos de um minuto)."""
    segundos = int(round(segundos))
    minutos, resto = divmod(segundos, 60)
    if minutos > 0:
        return f'{minutos}m{resto:02d}s'
    return f'{resto}s'


def formatar_tamanho(num_bytes):
    """Formata bytes como MB/GB legíveis."""
    mb = num_bytes / (1024 * 1024)
    if mb >= 1024:
        return f'{mb / 1024:.1f} GB'
    return f'{mb:.0f} MB'


#
# Configuração (config.json)
#

# Valores padrão do template do canal; o config.json pode sobrescrever
CONFIG_PADRAO = {
    'resolucao': '1920x1080',
    'fps': 30,
    'legenda': {
        'fonte': 'Nunito',
        'tamanho': 52,
        'cor_texto': '#F2EDE3',
        'cor_contorno': '#000000',
        'espessura_contorno': 3,
        'posicao_vertical': 'inferior',
        'margem_inferior': 80,
        'max_caracteres_linha': 42,
    },
    'trilha_volume_db': -18,
    'transicao_imagens_s': 0.5,
    'kenburns_zoom_max': 1.08,
    'fade_inicio_s': 1.5,
    'fade_fim_s': 1.5,
    # Ajustes usados apenas no modo --vertical (Shorts)
    'vertical': {
        'resolucao': '1080x1920',
        'legenda_tamanho': 64,
        'legenda_margem_inferior': 320,
        'legenda_max_caracteres_linha': 26,
    },
}


def carregar_config(caminho_config):
    """Carrega o config.json e mescla com os valores padrão."""
    config = json.loads(json.dumps(CONFIG_PADRAO))  # cópia profunda
    if caminho_config.exists():
        try:
            with open(caminho_config, 'r', encoding='utf-8') as f:
                usuario = json.load(f)
        except json.JSONDecodeError as e:
            erro(f'config.json inválido ({caminho_config}): {e}', 'validação')
        # Mescla rasa por seção para permitir sobrescrever só alguns campos
        for chave, valor in usuario.items():
            if isinstance(valor, dict) and isinstance(config.get(chave), dict):
                config[chave].update(valor)
            else:
                config[chave] = valor
    else:
        print(f'⚠ config.json não encontrado em {caminho_config}; '
              'usando os valores padrão do template.')
    return config


#
# FFprobe: duração e dimensões
#

def executar(cmd):
    """Executa um comando e retorna (código, stdout, stderr)."""
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def duracao_midia(arquivo):
    """Retorna a duração (em segundos) de um arquivo de áudio/vídeo."""
    codigo, saida, err = executar([
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        str(arquivo),
    ])
    if codigo != 0 or not saida:
        erro(f'Não consegui ler a duração de "{arquivo.name}". '
             f'O arquivo está corrompido? Detalhe: {err}', 'validação')
    return float(saida)


def dimensoes_imagem(arquivo):
    """Retorna (largura, altura) de uma imagem."""
    codigo, saida, err = executar([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
        str(arquivo),
    ])
    if codigo != 0 or not saida:
        erro(f'Não consegui ler as dimensões de "{arquivo.name}". '
             f'Detalhe: {err}', 'validação')
    largura, altura = saida.split(',')[:2]
    return int(largura), int(altura)


#
# Leitura e validação do SRT
#

RE_TEMPO_SRT = re.compile(
    r'(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*'
    r'(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})'
)


def srt_para_segundos(h, m, s, ms):
    """Converte campos de tempo SRT para segundos."""
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, '0')) / 1000


def ler_srt(caminho_srt):
    """
    Lê o arquivo SRT e retorna uma lista de legendas:
    [{'inicio': s, 'fim': s, 'texto': 'linha 1\nlinha 2'}, ...]
    """
    try:
        conteudo = caminho_srt.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError:
        conteudo = caminho_srt.read_text(encoding='latin-1')

    legendas = []
    # Blocos SRT são separados por linha em branco
    for bloco in re.split(r'\n\s*\n', conteudo.strip()):
        linhas = [l.strip('﻿').rstrip() for l in bloco.splitlines()]
        linhas = [l for l in linhas if l.strip()]
        if not linhas:
            continue
        # Procura a linha de tempos dentro do bloco
        indice_tempo = None
        for i, linha in enumerate(linhas):
            if RE_TEMPO_SRT.search(linha):
                indice_tempo = i
                break
        if indice_tempo is None:
            continue  # bloco sem timestamps (ex.: cabeçalho) é ignorado
        m = RE_TEMPO_SRT.search(linhas[indice_tempo])
        inicio = srt_para_segundos(m.group(1), m.group(2), m.group(3), m.group(4))
        fim = srt_para_segundos(m.group(5), m.group(6), m.group(7), m.group(8))
        texto = '\n'.join(linhas[indice_tempo + 1:]).strip()
        if not texto:
            continue
        if fim <= inicio:
            erro(f'Legenda com tempos invertidos no SRT '
                 f'(início {inicio:.3f}s >= fim {fim:.3f}s). '
                 'Corrija o arquivo legendas.srt.', 'validação')
        legendas.append({'inicio': inicio, 'fim': fim, 'texto': texto})

    if not legendas:
        erro('O arquivo legendas.srt não contém nenhuma legenda válida. '
             'Verifique se o formato é SRT padrão.', 'validação')
    return legendas


#
# Geração do arquivo ASS (legendas estilizadas queimadas no vídeo)
#

def hex_para_ass(cor_hex):
    """Converte '#RRGGBB' para o formato ASS '&H00BBGGRR'."""
    cor = cor_hex.lstrip('#')
    if len(cor) != 6:
        erro(f'Cor inválida no config.json: "{cor_hex}". '
             'Use o formato #RRGGBB.', 'validação')
    r, g, b = cor[0:2], cor[2:4], cor[4:6]
    return f'&H00{b}{g}{r}'.upper()


def segundos_para_ass(segundos):
    """Converte segundos para o formato de tempo ASS (H:MM:SS.cc)."""
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    resto = segundos % 60
    return f'{horas}:{minutos:02d}:{resto:05.2f}'


def quebrar_texto(texto, max_caracteres):
    """
    Reaplica a quebra de linhas respeitando o máximo de caracteres por
    linha definido no template (as quebras originais do SRT são refeitas).
    """
    palavras = ' '.join(texto.split())
    linhas = textwrap.wrap(palavras, width=max_caracteres) or [palavras]
    return r'\N'.join(linhas)


def gerar_ass(legendas, config, largura, altura, deslocamento_s,
              vertical, caminho_saida):
    """
    Gera o arquivo .ass com o estilo do canal. Todos os tempos são
    deslocados por `deslocamento_s` (tela preta inicial).
    """
    leg = config['legenda']
    vert = config['vertical']

    if vertical:
        tamanho = vert['legenda_tamanho']
        margem_v = vert['legenda_margem_inferior']
        max_chars = vert['legenda_max_caracteres_linha']
    else:
        tamanho = leg['tamanho']
        margem_v = leg['margem_inferior']
        max_chars = leg['max_caracteres_linha']

    # Alignment=2 no ASS: centralizado horizontalmente, ancorado embaixo.
    # MarginV controla a distância da borda inferior.
    estilo = (
        f"Style: Canal,{leg['fonte']},{tamanho},"
        f"{hex_para_ass(leg['cor_texto'])},&H000000FF,"
        f"{hex_para_ass(leg['cor_contorno'])},&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,{leg['espessura_contorno']},0,"
        f"2,60,60,{margem_v},1"
    )

    linhas = [
        '[Script Info]',
        'ScriptType: v4.00+',
        f'PlayResX: {largura}',
        f'PlayResY: {altura}',
        'ScaledBorderAndShadow: yes',
        'WrapStyle: 2',
        '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
        'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, '
        'ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding',
        estilo,
        '',
        '[Events]',
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, '
        'Effect, Text',
    ]

    for legenda in legendas:
        inicio = segundos_para_ass(legenda['inicio'] + deslocamento_s)
        fim = segundos_para_ass(legenda['fim'] + deslocamento_s)
        texto = quebrar_texto(legenda['texto'], max_chars)
        linhas.append(f'Dialogue: 0,{inicio},{fim},Canal,,0,0,0,,{texto}')

    caminho_saida.write_text('\n'.join(linhas) + '\n', encoding='utf-8')


#
# Validação da pasta do projeto
#

def validar_pasta(pasta, vertical):
    """
    Valida a estrutura da pasta do vídeo e retorna um dicionário com os
    caminhos e metadados necessários para a montagem.
    """
    if not pasta.is_dir():
        erro(f'A pasta "{pasta}" não existe ou não é um diretório.',
             'validação')

    # FFmpeg e FFprobe precisam estar instalados
    for programa in ('ffmpeg', 'ffprobe'):
        if shutil.which(programa) is None:
            erro(f'O programa "{programa}" não foi encontrado no PATH. '
                 'Instale o FFmpeg (veja o README.md, seção Instalação).',
                 'validação')

    # Arquivos obrigatórios (narração aceita .mp3 ou .wav)
    narracao = None
    for ext in EXTENSOES_AUDIO:
        candidato = pasta / f'narracao{ext}'
        if candidato.exists():
            narracao = candidato
            break
    if narracao is None:
        erro('Arquivo de narração não encontrado. Esperado '
             f'"narracao.mp3" ou "narracao.wav" em {pasta}/', 'validação')

    srt = pasta / 'legendas.srt'
    if not srt.exists():
        erro(f'Arquivo "legendas.srt" não encontrado em {pasta}/',
             'validação')

    trilha = None
    for ext in EXTENSOES_AUDIO:
        candidato = pasta / f'trilha{ext}'
        if candidato.exists():
            trilha = candidato
            break
    if trilha is None:
        erro('Arquivo de trilha musical não encontrado. Esperado '
             f'"trilha.mp3" ou "trilha.wav" em {pasta}/', 'validação')

    pasta_imagens = pasta / 'imagens'
    if not pasta_imagens.is_dir():
        erro(f'A pasta "{pasta_imagens}" não existe. Crie a pasta '
             '"imagens/" com as imagens numeradas (01.png, 02.png...).',
             'validação')

    # Imagens em ordem alfabética (01.png, 02.png, ...)
    imagens = sorted(
        p for p in pasta_imagens.iterdir()
        if p.suffix.lower() in EXTENSOES_IMAGEM
    )
    if len(imagens) < 3:
        erro(f'São necessárias pelo menos 3 imagens em {pasta_imagens}/ '
             f'(encontradas: {len(imagens)}).', 'validação')

    # Duração da narração e da trilha
    duracao_narracao = duracao_midia(narracao)
    duracao_trilha = duracao_midia(trilha)

    # SRT válido e coerente com a duração do áudio
    legendas = ler_srt(srt)
    fim_ultima = max(l['fim'] for l in legendas)
    # Meio segundo de tolerância para arredondamentos do TTS/encoder
    if fim_ultima > duracao_narracao + 0.5:
        erro(f'O SRT termina em {formatar_duracao(fim_ultima)}, mas a '
             f'narração tem só {formatar_duracao(duracao_narracao)}. '
             'As legendas não correspondem a esta narração — confira se '
             'os arquivos são do mesmo vídeo.', 'validação')

    # Proporção das imagens: avisa se alguma não for 16:9
    avisos = []
    for imagem in imagens:
        largura, altura = dimensoes_imagem(imagem)
        proporcao = largura / altura
        if abs(proporcao - 16 / 9) > TOLERANCIA_PROPORCAO * (16 / 9):
            avisos.append(
                f'⚠ A imagem "{imagem.name}" tem proporção '
                f'{largura}x{altura} (não é 16:9). Ela será cortada para '
                'preencher a tela.'
            )
    for aviso in avisos:
        print(aviso)

    if vertical:
        print('ℹ Modo vertical (Shorts): as imagens 16:9 serão cortadas '
              'no centro para 9:16.')

    etapa_ok(
        f'Validação concluída ({len(imagens)} imagens, narração '
        f'{formatar_duracao(duracao_narracao)}, SRT ok)'
    )

    return {
        'narracao': narracao,
        'srt': srt,
        'trilha': trilha,
        'imagens': imagens,
        'legendas': legendas,
        'duracao_narracao': duracao_narracao,
        'duracao_trilha': duracao_trilha,
    }


#
# Montagem do filtro FFmpeg (Ken Burns + crossfade + legendas + áudio)
#

def expressoes_kenburns(movimento, zoom_max, total_frames):
    """
    Retorna as expressões (z, x, y) do filtro zoompan para um movimento
    do ciclo Ken Burns. `on` é o número do frame atual dentro do clipe.
    """
    d = max(total_frames - 1, 1)
    progresso = f'(on/{d})'
    centro_x = '(iw-iw/zoom)/2'
    centro_y = '(ih-ih/zoom)/2'
    delta = zoom_max - 1.0

    if movimento == 'zoom-in':
        # Zoom lento de 100% até o máximo, centralizado
        return f'1+{delta}*{progresso}', centro_x, centro_y
    if movimento == 'zoom-out':
        # Começa no zoom máximo e volta a 100%
        return f'{zoom_max}-{delta}*{progresso}', centro_x, centro_y
    if movimento == 'pan-esquerda':
        # Zoom fixo, a "câmera" desliza da direita para a esquerda
        return (f'{zoom_max}',
                f'(iw-iw/zoom)*(1-{progresso})', centro_y)
    # pan-direita: desliza da esquerda para a direita
    return f'{zoom_max}', f'(iw-iw/zoom)*{progresso}', centro_y


def montar_filtro(dados, config, vertical, caminho_ass):
    """
    Monta o filter_complex completo do FFmpeg e retorna
    (filtro, duracao_total_do_video).
    """
    fps = config['fps']
    zoom_max = config['kenburns_zoom_max']
    transicao = config['transicao_imagens_s']
    fade_inicio = config['fade_inicio_s']
    fade_fim = config['fade_fim_s']
    volume_trilha = config['trilha_volume_db']

    if vertical:
        largura, altura = map(int, config['vertical']['resolucao'].split('x'))
    else:
        largura, altura = map(int, config['resolucao'].split('x'))

    n = len(dados['imagens'])
    duracao = dados['duracao_narracao']

    # Cada imagem fica em tela por `dur` segundos; os crossfades se
    # sobrepõem, então: n*dur - (n-1)*transicao = duração da narração.
    dur = (duracao + (n - 1) * transicao) / n
    if dur <= transicao + 0.1:
        erro(f'Imagens demais ({n}) para uma narração de '
             f'{formatar_duracao(duracao)}: cada imagem ficaria menos de '
             f'{transicao + 0.1:.1f}s em tela. Reduza o número de imagens.',
             'montagem visual')

    frames_por_imagem = max(int(round(dur * fps)), 2)

    # Renderiza o Ken Burns numa "tela" 2x maior e reduz no final —
    # isso suaviza o movimento do zoompan (evita tremidos de 1px).
    fator_super = 2
    super_l, super_a = largura * fator_super, altura * fator_super

    partes = []
    rotulos = []
    for i in range(n):
        movimento = MOVIMENTOS_KENBURNS[i % len(MOVIMENTOS_KENBURNS)]
        z, x, y = expressoes_kenburns(movimento, zoom_max, frames_por_imagem)
        # 1) Redimensiona cobrindo o quadro e corta o excesso (crop central)
        # 2) Aplica o zoompan (Ken Burns) gerando os frames do clipe
        # 3) Volta para a resolução final e normaliza timestamps p/ o xfade
        partes.append(
            f'[{i}:v]'
            f'scale={super_l}:{super_a}:force_original_aspect_ratio=increase'
            f':flags=lanczos,'
            f'crop={super_l}:{super_a},setsar=1,'
            f"zoompan=z='{z}':x='{x}':y='{y}'"
            f':d={frames_por_imagem}:s={super_l}x{super_a}:fps={fps},'
            f'scale={largura}:{altura}:flags=lanczos,'
            f'settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p'
            f'[v{i}]'
        )
        rotulos.append(f'[v{i}]')

    # Encadeia os crossfades: cada transição começa `transicao` segundos
    # antes do fim do clipe acumulado.
    atual = rotulos[0]
    deslocamento = dur - transicao
    for i in range(1, n):
        saida = f'[x{i}]' if i < n - 1 else '[vseq]'
        partes.append(
            f'{atual}{rotulos[i]}xfade=transition=fade'
            f':duration={transicao}:offset={deslocamento:.4f}{saida}'
        )
        atual = saida
        deslocamento += dur - transicao

    if n == 1:
        partes.append(f'{atual}null[vseq]')

    # Fades de entrada/saída + tela preta no início e no fim.
    # O conteúdo escurece/clareia e a tela preta é adicionada com tpad.
    duracao_total = TELA_PRETA_S + duracao + TELA_PRETA_S
    caminho_ass_escapado = escapar_caminho_filtro(caminho_ass)
    partes.append(
        f'[vseq]fade=t=in:st=0:d={fade_inicio},'
        f'fade=t=out:st={max(duracao - fade_fim, 0):.4f}:d={fade_fim},'
        f'tpad=start_duration={TELA_PRETA_S}:stop_duration={TELA_PRETA_S}'
        f':color=black,'
        # Legendas queimadas por cima de tudo (já deslocadas no .ass)
        f"subtitles=filename='{caminho_ass_escapado}'"
        f'[vfinal]'
    )

    # Áudio: narração inteira (volume principal) começa após a tela preta;
    # trilha ao fundo com volume reduzido, fade-in de 2s e fade-out de 3s.
    atraso_ms = int(TELA_PRETA_S * 1000)
    indice_narracao = n       # entradas 0..n-1 são as imagens
    indice_trilha = n + 1
    fade_out_trilha_inicio = max(duracao - 3.0, 0)
    partes.append(
        f'[{indice_narracao}:a]aresample=48000,'
        f'adelay={atraso_ms}|{atraso_ms}[anar]'
    )
    partes.append(
        f'[{indice_trilha}:a]aresample=48000,'
        # Corta a trilha (que pode estar em loop) no fim da narração
        f'atrim=0:{duracao:.4f},asetpts=PTS-STARTPTS,'
        f'volume={volume_trilha}dB,'
        f'afade=t=in:st=0:d=2,'
        f'afade=t=out:st={fade_out_trilha_inicio:.4f}:d=3,'
        f'adelay={atraso_ms}|{atraso_ms}[atri]'
    )
    partes.append(
        '[anar][atri]amix=inputs=2:duration=longest:normalize=0,'
        f'apad,atrim=0:{duracao_total:.4f}[afinal]'
    )

    return ';'.join(partes), duracao_total


def escapar_caminho_filtro(caminho):
    """
    Escapa um caminho para uso dentro de filter_complex do FFmpeg
    (necessário no Windows por causa de "C:\\...").
    """
    texto = str(caminho)
    texto = texto.replace('\\', '/')
    texto = texto.replace(':', r'\:')
    return texto


#
# Renderização com barra de progresso
#

def renderizar(comando, duracao_total):
    """
    Executa o FFmpeg exibindo o progresso (% do vídeo já renderizado).
    """
    processo = subprocess.Popen(
        comando,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ultima_porcentagem = -1
    # O FFmpeg escreve o progresso (-progress pipe:1) linha a linha
    for linha in processo.stdout:
        linha = linha.strip()
        if linha.startswith('out_time_us=') or linha.startswith('out_time_ms='):
            valor = linha.split('=', 1)[1]
            try:
                segundos = int(valor) / 1_000_000
            except ValueError:
                continue
            porcentagem = min(int(segundos / duracao_total * 100), 100)
            if porcentagem != ultima_porcentagem:
                print(f'\r⏳ Renderizando... {porcentagem}%',
                      end='', flush=True)
                ultima_porcentagem = porcentagem
        elif linha == 'progress=end':
            print('\r⏳ Renderizando... 100%', flush=True)

    processo.stdout.close()
    stderr = processo.stderr.read()
    processo.stderr.close()
    codigo = processo.wait()
    if codigo != 0:
        print()  # quebra de linha após a barra de progresso
        # Mostra só o final do stderr do FFmpeg (onde fica o erro real)
        detalhe = '\n'.join(stderr.strip().splitlines()[-15:])
        erro('O FFmpeg falhou durante a renderização. Últimas linhas do '
             f'log:\n{detalhe}\n\nCorrija o problema indicado acima e '
             'rode o comando novamente.', 'renderização')


#
# Programa principal
#

def main():
    parser = argparse.ArgumentParser(
        description='Monta o vídeo final a partir da pasta do projeto '
                    '(narração + legendas + imagens + trilha).'
    )
    parser.add_argument(
        'pasta',
        help='Pasta do vídeo (ex.: videos/2026-07-10-titulo-do-video/)'
    )
    parser.add_argument(
        '--vertical', action='store_true',
        help='Gera a versão 1080x1920 (Shorts) como final_vertical.mp4'
    )
    parser.add_argument(
        '--config', default=None,
        help='Caminho do config.json (padrão: ao lado deste script)'
    )
    args = parser.parse_args()

    inicio_execucao = time.time()
    pasta = Path(args.pasta).resolve()

    if args.config:
        caminho_config = Path(args.config).resolve()
    else:
        caminho_config = Path(__file__).resolve().parent / 'config.json'
    config = carregar_config(caminho_config)

    #
    # Etapa 1 — Validação
    #
    dados = validar_pasta(pasta, args.vertical)

    if args.vertical:
        largura, altura = map(int, config['vertical']['resolucao'].split('x'))
        nome_saida = 'final_vertical.mp4'
    else:
        largura, altura = map(int, config['resolucao'].split('x'))
        nome_saida = 'final.mp4'
    caminho_saida = pasta / nome_saida

    # Pasta temporária para os intermediários (nunca dentro da pasta do
    # vídeo — os originais não são tocados)
    with tempfile.TemporaryDirectory(prefix='montar_video_') as tmp:
        pasta_tmp = Path(tmp)

        #
        # Etapa 2 — Montagem visual (Ken Burns + crossfades)
        #
        caminho_ass = pasta_tmp / 'legendas.ass'
        gerar_ass(
            dados['legendas'], config, largura, altura,
            TELA_PRETA_S, args.vertical, caminho_ass
        )
        filtro, duracao_total = montar_filtro(
            dados, config, args.vertical, caminho_ass
        )
        # O filtro pode ficar longo com muitas imagens; passa por arquivo
        caminho_filtro = pasta_tmp / 'filtro.txt'
        caminho_filtro.write_text(filtro, encoding='utf-8')
        etapa_ok('Sequência visual montada (Ken Burns aplicado)')
        etapa_ok('Legendas queimadas')

        #
        # Etapa 3 — Comando FFmpeg (áudio mixado no mesmo passo)
        #
        comando = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error']
        # Entradas 0..n-1: um frame de cada imagem (o zoompan gera o clipe)
        for imagem in dados['imagens']:
            comando += ['-i', str(imagem)]
        # Entrada n: narração
        comando += ['-i', str(dados['narracao'])]
        # Entrada n+1: trilha (em loop caso seja mais curta que a narração)
        if dados['duracao_trilha'] < dados['duracao_narracao']:
            comando += ['-stream_loop', '-1']
        comando += ['-i', str(dados['trilha'])]

        comando += [
            '-filter_complex_script', str(caminho_filtro),
            '-map', '[vfinal]', '-map', '[afinal]',
            '-r', str(config['fps']),
            '-c:v', 'libx264', '-crf', '20', '-preset', 'medium',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart',
            '-progress', 'pipe:1', '-nostats',
            str(caminho_saida),
        ]
        etapa_ok(f"Áudio mixado (trilha {config['trilha_volume_db']}dB, "
                 'fades aplicados)')

        #
        # Etapa 4 — Renderização
        #
        renderizar(comando, duracao_total)

    #
    # Resumo final
    #
    tamanho = caminho_saida.stat().st_size
    duracao_video = duracao_midia(caminho_saida)
    tempo_render = time.time() - inicio_execucao
    print(
        f'✅ {nome_saida} pronto — {formatar_duracao(duracao_video)} · '
        f'{formatar_tamanho(tamanho)} · renderizado em '
        f'{formatar_duracao(tempo_render)}'
    )


if __name__ == '__main__':
    main()
