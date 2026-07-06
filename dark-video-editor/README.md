# Montar Vídeo — Editor automático de vídeos dark

Automatiza por completo a etapa de edição dos vídeos do canal: você coloca
os arquivos prontos (narração TTS, legendas SRT, imagens e trilha musical)
em uma pasta, roda **um único comando**, e o script entrega o vídeo final
renderizado, pronto para upload no YouTube — com o mesmo estilo visual em
todos os vídeos.

O que o script faz, na ordem:

1. Valida a pasta do vídeo (arquivos presentes, SRT coerente com o áudio,
   mínimo de 3 imagens, proporção 16:9).
2. Distribui as imagens ao longo da narração, com crossfade de 0,5s entre
   elas e efeito **Ken Burns** (zoom-in → pan-esquerda → zoom-out →
   pan-direita, em ciclo) — nenhuma imagem fica parada.
3. Queima as legendas no vídeo (hardcoded) com o estilo do canal definido
   no `config.json`.
4. Mixa o áudio: narração em volume principal + trilha ao fundo (−18 dB),
   com fade-in de 2s e fade-out de 3s na trilha.
5. Adiciona 1,5s de tela preta com fade no início e no fim.
6. Renderiza em 1920x1080, 30 fps, H.264 CRF 20, AAC 192 kbps — otimizado
   para YouTube (`+faststart`).

A renderização é feita **diretamente pelo FFmpeg** (sem MoviePy), então é
rápida mesmo para vídeos de 8–10 minutos. Os arquivos originais da pasta
**nunca são modificados nem apagados**.

---

## Instalação

### 1. Python

É necessário Python 3.8 ou mais novo. Verifique com:

```bash
python --version    # ou python3 --version
```

Não há dependências Python para instalar — o script usa só a biblioteca
padrão (o `requirements.txt` existe apenas para documentar isso).

### 2. FFmpeg

O FFmpeg faz todo o trabalho pesado e precisa estar instalado no PATH.

**Windows**

1. Baixe a versão "release full" em <https://www.gyan.dev/ffmpeg/builds/>
   (arquivo `ffmpeg-release-full.7z`).
2. Extraia para `C:\ffmpeg`.
3. Adicione `C:\ffmpeg\bin` ao PATH: Iniciar → "Editar as variáveis de
   ambiente do sistema" → Variáveis de Ambiente → em "Path", clique em
   Editar → Novo → cole `C:\ffmpeg\bin` → OK.
4. Feche e reabra o terminal e confirme com `ffmpeg -version`.

   Alternativa mais simples (se você usa o winget):

   ```powershell
   winget install Gyan.FFmpeg
   ```

**macOS** (com [Homebrew](https://brew.sh)):

```bash
brew install ffmpeg
```

**Linux (Ubuntu/Debian/Mint):**

```bash
sudo apt update && sudo apt install -y ffmpeg
```

Confirme a instalação (qualquer sistema):

```bash
ffmpeg -version
ffprobe -version
```

### 3. Fonte Nunito (recomendado)

As legendas usam a fonte **Nunito**. Baixe em
<https://fonts.google.com/specimen/Nunito> e instale no sistema
(no Windows: clique direito no `.ttf` → Instalar; no macOS: duplo clique →
Instalar Fonte; no Linux: copie para `~/.local/share/fonts` e rode
`fc-cache -f`). Se a fonte não estiver instalada, o FFmpeg usa uma fonte
substituta automaticamente — o vídeo sai, mas o visual muda.

---

## Estrutura de pastas

```
projeto/
├── montar_video.py
├── config.json          # template do canal (definido uma vez)
└── videos/
    └── 2026-07-10-titulo-do-video/
        ├── narracao.mp3     # narração TTS (.mp3 ou .wav)
        ├── legendas.srt     # legendas sincronizadas com a narração
        ├── trilha.mp3       # música instrumental (.mp3 ou .wav)
        └── imagens/
            ├── 01.png       # imagens 16:9, nomeadas em ordem
            ├── 02.png
            └── ...
```

Os nomes `narracao`, `legendas.srt`, `trilha` e a pasta `imagens/` são
obrigatórios. As imagens são usadas em ordem alfabética — por isso a
numeração `01`, `02`, `03`... (com zero à esquerda).

---

## Uso

Vídeo horizontal (YouTube, 1920x1080):

```bash
python montar_video.py videos/2026-07-10-titulo-do-video/
```

Saída: `videos/2026-07-10-titulo-do-video/final.mp4`

Versão vertical (Shorts, 1080x1920, crop central + legendas maiores):

```bash
python montar_video.py videos/2026-07-10-titulo-do-video/ --vertical
```

Saída: `videos/2026-07-10-titulo-do-video/final_vertical.mp4`

Exemplo de saída no terminal:

```
✓ Validação concluída (12 imagens, narração 8m42s, SRT ok)
✓ Sequência visual montada (Ken Burns aplicado)
✓ Legendas queimadas
✓ Áudio mixado (trilha -18dB, fades aplicados)
⏳ Renderizando... 100%
✅ final.mp4 pronto — 8m45s · 412 MB · renderizado em 3m12s
```

Se algo estiver errado (arquivo faltando, SRT maior que a narração,
menos de 3 imagens...), o script para **antes de renderizar** com uma
mensagem em português dizendo exatamente o que corrigir.

---

## Ajustando o template do canal (config.json)

O `config.json` fica ao lado do `montar_video.py` e vale para todos os
vídeos. Campos disponíveis:

| Campo | O que controla | Padrão |
|---|---|---|
| `resolucao` | Resolução do vídeo horizontal | `1920x1080` |
| `fps` | Quadros por segundo | `30` |
| `legenda.fonte` | Fonte das legendas | `Nunito` |
| `legenda.tamanho` | Tamanho da fonte (horizontal) | `52` |
| `legenda.cor_texto` | Cor do texto (#RRGGBB) | `#F2EDE3` |
| `legenda.cor_contorno` | Cor do contorno | `#000000` |
| `legenda.espessura_contorno` | Espessura do contorno | `3` |
| `legenda.margem_inferior` | Distância da borda inferior (px) | `80` |
| `legenda.max_caracteres_linha` | Quebra de linha automática | `42` |
| `trilha_volume_db` | Volume da trilha em relação à narração | `-18` |
| `transicao_imagens_s` | Duração do crossfade entre imagens | `0.5` |
| `kenburns_zoom_max` | Zoom máximo do Ken Burns (1.08 = 8%) | `1.08` |
| `fade_inicio_s` / `fade_fim_s` | Duração dos fades de abertura/fechamento | `1.5` |
| `vertical.*` | Overrides do modo Shorts (resolução, legenda maior) | — |

Observações:

- A tela preta de abertura/fechamento é fixa em 1,5s (constante
  `TELA_PRETA_S` no topo do `montar_video.py`, fácil de ajustar).
- Se a trilha for mais curta que a narração, ela é repetida em loop
  automaticamente e cortada no fim, com fade-out.
- As quebras de linha do SRT são refeitas pelo script respeitando
  `max_caracteres_linha`, para que as legendas fiquem uniformes.

---

## Problemas comuns

- **"O programa ffmpeg não foi encontrado no PATH"** — o FFmpeg não está
  instalado ou o terminal não foi reaberto após a instalação. Veja a
  seção Instalação.
- **Legendas com fonte diferente** — instale a fonte Nunito no sistema
  (seção Instalação, passo 3).
- **"O SRT termina em X, mas a narração tem só Y"** — o `legendas.srt`
  não corresponde ao `narracao.mp3` da pasta. Confira se os dois arquivos
  são do mesmo vídeo.
- **Renderização lenta** — o Ken Burns é renderizado em resolução dobrada
  para ficar suave; num notebook comum, espere algo em torno de metade da
  duração do vídeo. Para testes rápidos, troque `-preset medium` por
  `-preset veryfast` no `montar_video.py` (função `main`, montagem do
  comando FFmpeg).
