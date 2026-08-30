# RecordBate

App de escritorio que vigila canales de **Twitch, Kick, Stripchat y Chaturbate**, graba los directos automáticamente en cuanto empiezan y te deja organizar el resultado: miniaturas, renombrar, filtrar, reproducir y enviar a la papelera. La interfaz es NiceGUI en una ventana nativa, pero el motor funciona igual sin ella.

> Uso personal. Los términos de servicio de estas plataformas no permiten grabar ni redistribuir el contenido; los archivos se quedan en tu disco.

## Requisitos

- Windows con Python 3.11+
- ffmpeg en el PATH (`winget install Gyan.FFmpeg`)

## Instalación

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## Uso

Doble clic en `run.bat` (o `python app.py`, que abre http://127.0.0.1:8211).

- **Panel** — pega la URL de un canal y pulsa Añadir. Con *Auto* activado empieza a grabar en cuanto el canal se pone en vivo; también puedes forzarlo con *Grabar ahora*. Cada canal guarda su propio registro: el comando exacto que se lanzó y por qué se cerró.
- **Biblioteca** — todas las grabaciones con miniatura, reproductor integrado, renombrado y papelera. Un `.ts` (grabación interrumpida) se convierte a MP4 con un clic.
- **Ajustes** — carpeta, calidad, intervalo de comprobación, plantilla de nombres, acceso desde la red local y actualización de yt-dlp/streamlink.

Minimizar manda la ventana a la bandeja del sistema y sigue grabando de fondo.

## Sin GUI (server / bot / CLI)

El motor no necesita interfaz. Con `recordbate-cli.bat` (o `python -m recordbate.cli`):

```
recordbate-cli dashboard           # panel interactivo en la terminal
recordbate-cli run                 # daemon; Ctrl+C finaliza las grabaciones y sale
recordbate-cli now                 # qué se está grabando, desde otra terminal
recordbate-cli stop <usuario>      # para una grabación en curso (o 'all')
recordbate-cli add <url>           # añade un canal
recordbate-cli auto <usuario> off  # activa/desactiva auto-grabar
recordbate-cli remove <usuario>    # quita un canal
recordbate-cli list                # lista los canales
recordbate-cli status              # quién está en vivo ahora
recordbate-cli offset <ms>         # ajuste fino de audio (normalmente no hace falta)
```

`now` y `stop` hablan con el proceso que esté grabando a través de `data/`, así que funcionan desde otra terminal mientras la GUI o el daemon siguen abiertos.

El **dashboard** es el Panel en versión terminal, en tiempo real:

| Tecla | Acción |
|---|---|
| ↑ ↓ / 1-9 | seleccionar canal |
| **+** (o n) | añadir un canal pegando la URL |
| Supr / ⌫ | quitar el canal seleccionado |
| espacio | auto-grabar on/off del seleccionado |
| A | auto-grabar on/off en todos |
| r | grabar ya el seleccionado |
| s | parar la grabación del seleccionado |
| x | parar todas las grabaciones |
| v | vigilancia global on/off |
| q | salir (finaliza lo que esté grabando) |

No dejes `dashboard` y `run` abiertos a la vez: los dos grabarían lo mismo.

## Cómo funciona

Un servicio asyncio comprueba cada X segundos quién está en vivo contra las APIs públicas de cada sitio. Si una no contesta, lo intenta igualmente y deja que el grabador decida.

La captura va a un `.ts`, que sobrevive a un corte de luz o a un cierre a lo bruto:

- **Twitch y Kick** con streamlink.
- **Stripchat y Chaturbate** con ffmpeg directo, que baja el HLS y lo muxea copiando audio y vídeo tal cual, sin recodificar nada.

Al terminar, remux a MP4 (también copia pura), miniatura y a la biblioteca.

**Sincronía de audio.** Los cam sites publican el audio y el vídeo como dos playlists HLS separados. Si se le pasan a ffmpeg como dos inputs, desplaza cada uno a cero por su cuenta: abre primero el vídeo, tarda un par de segundos en sondearlo, y para entonces el live-edge del audio ya avanzó — el audio queda adelantado esa cantidad, distinta en cada grabación. La solución es dárselo como **un solo input**: un pequeño master playlist local con la variante de vídeo elegida y su pista de audio, de forma que ffmpeg aplica un único desplazamiento común y la línea de tiempo compartida de la fuente se conserva intacta (verificado por correlación cruzada: de 1.6 s de adelanto a alineación exacta). El ajuste manual de Ajustes queda como retoque fino y se aplica al convertir a MP4, desplazando timestamps sin tocar las muestras.

Los procesos de grabación quedan atados a un job object de Windows: si la app muere de golpe, el sistema se los lleva por delante en vez de dejar ffmpeg grabando huérfano.

## Dónde queda todo

- `grabaciones/<streamer>/` — los vídeos (configurable en Ajustes).
- `data/` — configuración, lista de canales, caché de la biblioteca y `recordbate.log`.

Ninguna de las dos se sube al repositorio.
