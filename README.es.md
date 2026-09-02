# Recam

**v0.0.1** · [English documentation](README.md) · [Tutorial](TUTORIAL.es.md) · [Tutorial in English](TUTORIAL.md)

> **⚠ Versión de prueba.** Esta es una versión de prueba temprana y por ahora graba **solo Chaturbate**. Puede que algo falle — cualquier cosa que puedas contar (fallos, partes confusas, ideas) ayuda de verdad a seguir con el desarrollo. ¡Gracias por probarla!

App de escritorio que vigila canales de **Chaturbate**, graba los directos automáticamente en cuanto empiezan y te ayuda a organizar el resultado: vista previa en vivo mientras graba, miniaturas, reproductor integrado con reanudación, renombrado, filtros y borrado seguro a la papelera. La interfaz es NiceGUI en ventana nativa, pero el motor funciona igual sin ella. (El soporte para más plataformas ya está dentro del motor y llegará en futuras versiones.)

> Uso personal. Los términos de servicio de estas plataformas no permiten grabar ni redistribuir el contenido; los archivos se quedan en tu disco.

El idioma por defecto de la interfaz es el inglés; cámbialo a español en **Settings → Language**.

## Requisitos

- Windows con Python 3.11+
- ffmpeg en el PATH (`winget install Gyan.FFmpeg`)

## Instalación

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## Uso

Doble clic en `run.bat` (o `python app.py`, que sirve http://127.0.0.1:8211).

- **Panel** — pega la URL de un canal y pulsa Añadir. Con *Auto* activado empieza a grabar en cuanto el canal se pone en vivo; también puedes forzarlo con *Grabar ahora*. Los canales se ven como cuadrícula de tarjetas ordenadas por utilidad (grabando primero, luego en vivo), las que graban con su fotograma de vista previa. El feed de actividad muestra lo que pasó mientras no mirabas.
- **Biblioteca** — grabaciones agrupadas en una sección plegable por perfil, como tarjetas con miniatura y duración. Clic en una tarjeta para reproducir en el reproductor integrado (saltos de ±10 s, velocidad, recuerda tu volumen y por dónde ibas). La selección múltiple manda varias grabaciones a la papelera de una vez. Los `.ts` sin procesar (grabaciones interrumpidas) se convierten a MP4 con un clic.
- **Ajustes** — carpeta de destino, calidad, intervalo de comprobación, plantilla de nombres, idioma de la interfaz (inglés/español), inicio con Windows, acceso LAN (mirar el panel desde el móvil) y actualización de yt-dlp/streamlink con un clic.

Minimizar minimiza normal. La **X** de la ventana pregunta si esconder la app a la bandeja (sigue grabando de fondo) o cerrarla del todo — al cerrar, las grabaciones en curso se finalizan a MP4 limpio.

## Sin GUI (server / bot / CLI)

El motor no necesita interfaz. Con `recam-cli.bat` (o `python -m recam.cli`):

```
recam-cli dashboard           # panel interactivo en la terminal
recam-cli run                 # daemon; Ctrl+C finaliza las grabaciones y sale
recam-cli now                 # qué se está grabando, desde otra terminal
recam-cli stop <usuario>      # para una grabación en curso (o 'all')
recam-cli add <url>           # añade un canal
recam-cli auto <usuario> off  # activa/desactiva auto-grabar
recam-cli remove <usuario>    # quita un canal
recam-cli list                # lista los canales
recam-cli status              # quién está en vivo ahora
recam-cli offset <ms>         # ajuste fino de audio (normalmente no hace falta)
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

Las peticiones a un mismo sitio van espaciadas (nada de ráfagas con muchos canales), y si aun así llega un 429 la app entra en pausa automática con espera creciente — insistir solo alarga el castigo.

La captura va a un `.ts`, que sobrevive a un corte de luz o a un cierre a lo bruto: ffmpeg baja el HLS directamente y muxea audio y vídeo tal cual, sin recodificar. (El motor también lleva soporte vía streamlink para otras plataformas, desactivado en esta versión de prueba.)

Mientras graba, cada ~15 segundos se saca un fotograma reciente del archivo en crecimiento como vista previa. Al terminar: remux a MP4 (también copia pura), miniatura y a la biblioteca.

**Sincronía de audio.** Los cam sites publican el audio y el vídeo como dos playlists HLS separados. Si se le pasan a ffmpeg como dos inputs, desplaza cada uno a cero por su cuenta: abre primero el vídeo, tarda un par de segundos en sondearlo, y para entonces el live-edge del audio ya avanzó — el audio queda adelantado esa cantidad, distinta en cada grabación. La solución es **un solo input**: un pequeño master playlist local con la variante de vídeo elegida y su pista de audio, de forma que ffmpeg aplica un único desplazamiento común y la línea de tiempo compartida de la fuente se conserva intacta (verificado por correlación cruzada: de 1.6 s de adelanto a alineación exacta). El ajuste manual de Ajustes queda como retoque fino y se aplica al convertir a MP4, desplazando timestamps sin tocar las muestras.

Los procesos de grabación quedan atados a un job object de Windows: si la app muere de golpe, el sistema los mata en vez de dejar ffmpeg grabando huérfano.

## Dónde queda todo

- `grabaciones/<streamer>/` — los vídeos (configurable en Ajustes).
- `data/` — configuración, lista de canales, caché de la biblioteca y `recam.log`.

Ninguna de las dos se sube al repositorio.

## Autor y feedback

Hecho por **Emy69**. Esta es una versión de prueba temprana — sigue el proyecto y envía tu feedback:

- Patreon: https://www.patreon.com/c/emy69
- Discord: https://discord.com/invite/ku8gSPsesh
- GitHub: https://github.com/Emy69
- X: https://x.com/dev_emy
- Buy Me a Coffee: https://buymeacoffee.com/emy_69
