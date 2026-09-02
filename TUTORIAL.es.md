# RecordBate — Tutorial

*English version: [TUTORIAL.md](TUTORIAL.md)*

> **⚠ Versión de prueba** — la v0.0.1 graba **solo Chaturbate** por ahora. Cualquier comentario que puedas dejar ayuda de verdad al desarrollo. ¡Gracias por probarla!

Guía paso a paso desde cero hasta tu primera grabación automática.

## 1. Instala los requisitos

Necesitas **Python 3.11+** y **ffmpeg** en Windows.

```
winget install Python.Python.3.12
winget install Gyan.FFmpeg
```

Cierra y vuelve a abrir la terminal después, para que el PATH se actualice.

## 2. Prepara la app

Desde la carpeta de RecordBate:

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Eso crea un entorno de Python privado e instala todo lo que la app necesita (NiceGUI, streamlink, yt-dlp, etc.).

## 3. Primer arranque

Doble clic en **`run.bat`**. Se abre una ventana nativa con tres pestañas: **Panel**, **Library** y **Settings**.

- `run.bat` arranca la app en silencio (sin consola).
- `run-debug.bat` la arranca con consola — úsalo si algo falla y quieres ver por qué.

La interfaz está en inglés por defecto. Para pasarla a español: **Settings → Language → Español → Save settings**, y recarga.

## 4. Añade tu primer canal

1. Copia la URL de un canal de Chaturbate desde el navegador — por ejemplo `https://chaturbate.com/unmodelo`.
2. Pégala en la caja de arriba del **Panel** y pulsa **Añadir** (o Enter).

El canal aparece como tarjeta con su badge de plataforma y su estado: **EN VIVO**, **OFFLINE**, **DESCONOCIDO** o **GRABANDO**.

## 5. Grabación automática

Cada tarjeta tiene un interruptor **auto** (abajo a la derecha). Con él activado, la app comprueba el canal en cada ciclo (60 s por defecto) y empieza a grabar en cuanto se pone en vivo — sin tocar nada. Las piezas:

- **Vigilancia automática** (interruptor de arriba): el interruptor maestro. Apagado = ni comprobaciones ni grabaciones nuevas.
- **Comprobar ahora**: chequea todos los canales al momento y te dice cuántos hay en vivo.
- **Grabar ahora** (⏺ en una tarjeta): intenta grabar ya, aunque el estado diga DESCONOCIDO. Si no hay directo, el intento se cancela solo.
- **Parar** (⏹ en una tarjeta grabando): detiene y guarda. La auto-grabación de ese canal se pausa 10 minutos para que la app no te lleve la contraria.

Mientras un canal graba, su tarjeta muestra un **fotograma de vista previa en vivo** que se refresca cada ~15 segundos, más el contador de tiempo y tamaño. El **feed de actividad** de abajo guarda el historial corto: qué empezó, qué se guardó, qué falló.

## 6. La Biblioteca

Las grabaciones se agrupan en **una sección plegable por perfil**. Dentro se ven como tarjetas con miniatura y duración.

- **Clic en una tarjeta** para reproducirla en el reproductor integrado: saltos de ±10 s, velocidad (1× → 2×), y recuerda tu volumen y por dónde ibas.
- El **menú ⋮** de cada tarjeta: abrir con tu reproductor del sistema, mostrar en la carpeta, renombrar, enviar a la papelera.
- **Ordenar** (recientes, más grandes, más largas…), **buscar** y el **filtro por streamer** arriba.
- **Borrado en lote**: pulsa el botón ☑, marca varias grabaciones (clic en la tarjeta también marca), y *A la papelera*. Una sola confirmación, y todo va a la papelera de Windows — nunca se borra en duro.
- Una tarjeta marcada **SIN PROCESAR** es un `.ts` inacabado (un cuelgue o corte de luz). Clic para convertirlo a un MP4 limpio.

## 7. Ajustes que conviene conocer

- **Carpeta de grabaciones** — a dónde van los vídeos. La plantilla por defecto `{streamer}/{date} {time} [{platform}]` los ordena en una carpeta por perfil.
- **Calidad** — máxima / 1080p / 720p / 480p.
- **Grabaciones simultáneas** — el tope de capturas a la vez (4 por defecto).
- **Inicio con Windows** — la app arranca al iniciar sesión y se pone a vigilar tus canales con auto.
- **Acceso LAN** — con él activado (y un reinicio), abre `http://<ip-de-tu-pc>:8211` desde el móvil para ver el panel en remoto.
- **Actualizar yt-dlp y streamlink** — estos sitios cambian a menudo; si una plataforma deja de grabar, actualiza aquí y reinicia.

## 8. Cerrar, esconder y la bandeja

- **Minimizar** minimiza a la barra de tareas, nada más.
- La **X** de la ventana pregunta: **Esconder** (la app sigue grabando de fondo; se recupera desde el icono de la bandeja) o **Cerrar del todo** (las grabaciones en curso se finalizan y guardan antes).
- El menú del icono de bandeja también tiene *Mostrar RecordBate* y *Salir*.

## 9. Línea de comandos (opcional)

Todo funciona sin la GUI. Desde la carpeta de la app, `recordbate-cli.bat`:

```
recordbate-cli dashboard      # panel de terminal a pantalla completa, con teclas
recordbate-cli run            # daemon sin interfaz (para un server o dejarlo de fondo)
recordbate-cli now            # ver qué se graba desde otra terminal
recordbate-cli stop all       # parar todas las capturas (cada una se finaliza)
```

La CLI comparte configuración y canales con la GUI. No ejecutes la GUI y `run`/`dashboard` a la vez — grabarían lo mismo por duplicado.

## 10. Problemas frecuentes

| Síntoma | Qué significa / qué hacer |
|---|---|
| Un canal muestra *límite de peticiones (429)* | El sitio te está limitando. La app espera y reintenta sola; no hay que hacer nada. |
| Un canal de Stripchat dice *emisión cifrada* | Ese directo usa el cifrado Mouflon de Stripchat y no se puede grabar sin una clave que el sitio rota constantemente. No tiene arreglo desde aquí. |
| *NO guardado — solo X KB* | El intento nunca recibió datos reales: el directo no era público o terminó al instante. Los restos se limpian solos. |
| Una plataforma deja de grabar de repente | El sitio cambió algo. **Ajustes → Actualizar yt-dlp y streamlink**, reinicia y prueba. |
| Quieres saber por qué se cerró una grabación | Menú de la tarjeta → **Ver registro**: el comando exacto, el código de salida y las últimas líneas del grabador. El historial completo está en `data/recordbate.log`. |
| Audio desincronizado | Ya no debería pasar (ver el README). Si un archivo concreto se oye movido, reprodúcelo en VLC y ajusta con `j`/`k`, o pon un ajuste en Ajustes para futuras conversiones. |
