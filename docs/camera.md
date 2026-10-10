# Camera and microphone

A USB webcam on the machine that runs IoT Center Lite adds a **Camera** tab to the dashboard. It shows the live
picture at the camera's own resolution and quality, plus what the camera notices, as numbers and events:

- **Motion**: how much of the picture moves, with boxes around the moving parts.
- **Room light**: when a light is switched on or off.
- **Zones**: parts of the picture you draw, each watched for one thing: water on a floor, a status light or a
  burner's flame, motion in an area. A zone can be switched off without deleting it.
- **Recordings** (optional): a short clip of each motion event, kept for 30 days on this machine, to watch
  in the Camera tab.
- **Sound**, with the webcam's microphone: how loud it is, a **Listen** button, and alarms. Smoke and CO alarms
  are recognised by their beeping rhythm, as are other beeping and a detector chirping for a new battery.
  While an alarm sounds, every dashboard view shows a red banner.

Without recordings, nothing is written to disk: frames and sound only ever live in memory.

```
webcam ──V4L2, MJPEG──▶ capture thread ──▶ newest frame ──┬──▶ /api/camera/stream.mjpg ──▶ <img> in the Camera tab
                                                          ├──▶ /api/camera/snapshot.jpg (saved by the browser)
                                                          └──▶ ActivityMonitor + zones, 5×/s ──▶ `camera` events
microphone ──arecord, 16 kHz──▶ capture thread ──┬──▶ SoundAnalyzer ──▶ `sound` events (and the alarm banner)
                                                 └──▶ /api/sound/live.wav (Listen)
```

## Turn it on

Find the camera. On Linux, `ls /dev/v4l/by-id` lists webcams by a name that survives reboots and replugging;
`…-video-index0` is the picture (`index1` is metadata). `/dev/video0` works too, but its number can change.
Find its microphone with `arecord -l`: the card's name (for example `C960`) gives `plughw:CARD=C960,DEV=0`.

**Docker** (from the repository root): `make lite-docker-camera`, or set it up once in `lite/.env`
(git-ignored), so a plain `docker compose up -d` includes it:

```bash
COMPOSE_FILE=compose.yaml:compose.usb.yaml:compose.camera.yaml
CAMERA_DEVICE=v4l/by-id/usb-<camera>-video-index0   # the path under /dev; default video0
CAMERA_NAME=Basement                                 # the tab's title
MICROPHONE_DEVICE=plughw:CARD=<card>,DEV=0           # optional: sound, alarms, Listen
CAMERA_RECORD=true                                   # optional: a clip of each motion, kept 30 days
```

The [overlay](../lite/compose.camera.yaml) adds OpenCV and `arecord` to the image. It mounts the host's `/dev`
read-only, so the camera can be unplugged and plugged back in at any time, and lets the container open video and
sound devices and nothing else. The container starts without them and picks them up when they appear.

**Python**: `pip install -e ".[lite,camera]"`, then `iotcenter lite --camera /dev/video0 --microphone
plughw:CARD=<card>,DEV=0`. The microphone needs `arecord` (`sudo apt install alsa-utils`). Your user needs to be
in the `video` and `audio` groups (`sudo usermod -aG video,audio $USER`, then log in again).

**No webcam?** `make lite-camera-demo` (`iotcenter lite --camera demo --microphone demo`) plays a synthetic room:

- a ball crosses the floor for 20 s of every minute, and the lights go off for 6 s of every minute;
- every 5 minutes, a puddle spreads on the left of the floor, stays 3 minutes and dries;
- a smoke alarm sounds for 9 s every 2 minutes, a detector chirps every 40 s, and something bangs now and then.

Draw a floor zone at the lower left and a light zone on the red lid to see the zones work. It is also the scene
to use for screenshots.

Settings: `IOT_CAMERA_DEVICE`, `IOT_CAMERA_NAME`, `IOT_CAMERA_WIDTH`/`HEIGHT` (1920×1080), `IOT_CAMERA_FPS` (30),
`IOT_MICROPHONE_DEVICE` and `IOT_CAMERA_RECORD` (`--record`), `IOT_CAMERA_RECORD_DAYS` (30) and
`IOT_CAMERA_RECORD_MAX_GB` (20) ([configuration](configuration.md)).

## The picture

**The picture is the camera's own.** Most webcams compress every frame to JPEG themselves (MJPEG). IoT Center
asks for that format at full resolution and hands the camera's JPEG bytes to the browser untouched: nothing is
decoded or re-encoded, so there is no quality loss and almost no CPU cost. With an EMEET C960 (1080p) that is
1920×1080 at 15 to 30 fps, about 220 KB a frame, 27 to 35 Mbit/s at full rate, and 0.4% of one CPU core to
capture. A camera that only offers raw frames (YUYV) still works: OpenCV decodes them and IoT Center encodes
JPEG at quality 90.

**Every viewer gets the newest frame.** A slow connection skips frames rather than falling behind, so the
picture is never late, only less smooth. The frame-rate buttons (Full, 15, 5, 1 fps) make the server send fewer
frames, which saves data without re-encoding; 5 fps is about a third of the full rate's data. The picture streams
only while the Camera tab is open and the browser tab is visible. Motion, zones and sound travel on the
dashboard's existing live stream, so a dashboard tab needs two connections (three while listening). That matters
on plain HTTP, where a browser allows six per server.

## What the camera notices

Five times a second, the [`ActivityMonitor`](../src/iotcenter/vision.py) decodes the newest frame at a quarter of
its size: 480×270 for 1080p. A JPEG decoder can do that directly, in about 3 ms. It compares a grey copy with a
background that slowly adapts to the scene, and works out:

- **Motion**: the share of the picture that differs from the background by more than 25 grey levels (of 255),
  and boxes around the larger moving areas. Motion over 0.4% of the picture, twice in a row, starts an event.
  The event ends after 3 seconds below 0.15%, and keeps its start, end, peak and the zones it touched.
- **Room light**: the picture's mean brightness. A change of 12 points within a second means a light was
  switched on or off, and that is an event too. The whole picture changes then, and keeps changing while the
  camera's auto-exposure settles, so motion is ignored for 2 seconds rather than reported.

With a 1080p camera and a few zones, the whole of it takes about 4% of one CPU core.

## Zones

Draw a zone with **Add zone**: drag a rectangle over the picture, name it, and pick what it watches. The switch in
front of each zone stops watching it without deleting it. In the toolbar, **Zone outlines** shows or hides the zones
on the picture, and **Motion boxes** the boxes around whatever moves (there are none while nothing moves). Zones are kept in `camera-zones.json` next to the database (`data/`
locally, the `lite-data` volume in Docker), as names and rectangles only, never a picture.
[`zones.py`](../src/iotcenter/zones.py) watches each kind:

- **Water on the floor.** Wet concrete is darker than dry, and standing water can mirror a lamp. A floor zone
  first learns the dry floor (10 s), then, once a second, compares each part of it with that dry floor, on a
  sharper copy of the picture than motion uses (960×540 for 1080p, where a small spill is tens of pixels). A
  patch 12% darker (or 35% brighter) than the dry floor, of at least 30 pixels of that copy (a spill a hand
  across, 4 m away) whatever the zone's size, that stays for 30 s is **possible water**: an event, a red outline,
  a red box around the patch, and a red banner on every dashboard view. The zone is dry again after a minute
  with the patch under half that size.
- **Water already there, or a stain?** When a floor zone starts watching (a new zone, a restart, a new
  lighting), it also looks for water that is already on the floor, with nothing to compare it to: patches
  darker than the floor around them (by 12 to 55%), of the floor's own colour (painted plates, a bluish tank or
  metal are bluer), with clear edges (a shadow's are soft), that do not touch the zone's edge (the base of a
  tank or a furnace only reaches into it) and are not the rim of something black (a drain cover). One picture
  cannot tell water from a stain, so such a patch is **water or stain?**: amber, not the red alarm, and
  watched. If it gets lighter, it is water drying, and it clears when dry; if it spreads, it is reported as
  water; if it stays exactly as it is for 30 minutes, it is a stain, and becomes part of the floor. **It's a
  stain** (the same button as **Floor is dry**) settles it at once.
- **A floor drain.** Some water around a drain is normal: a furnace's condensate line often ends there. A
  **floor drain** zone reports only water pooling over 15% of the zone (a backup or a blocked drain), and does
  not look for water already there. The dry floor follows slow changes,
  such as daylight and dust, over about half an hour, but never towards a patch. The floors wait 20 s after
  anything moves anywhere in the picture, or someone is in the zone. They also wait right after a room light
  switches, and in the dark.
- **Each lighting has its own dry floor.** The room under its lamps, daylight only, a second lamp: each one looks
  different, and the camera's auto-exposure changes the brightness of all of them. So the zones recognise each
  lighting by a tiny copy of the whole picture, and keep a reference picture of it to correct the exposure,
  remembering up to four. A floor zone learns the dry floor once for each lighting. The point is the leak that
  starts at night with the lights off: when the lights come back on, the morning is recognised as the lighting
  it learned, and the puddle is reported instead of being learned as the new normal. A new lighting, or a camera
  that was moved, shows as an event; after moving the camera, redraw the zones.
- **A status light or flame.** How lit it is: its colour (the brightest colour channel minus the dimmest, at
  the zone's brightest 1%), or for a white LED, how far its brightest spot stands out of the zone. A grey or
  silver panel has almost no colour, in daylight, under the room's lights or in the dark, while a blue LED or a
  burner's flame has a lot. Status LEDs are a pixel or two wide, so light zones are judged on the sharper copy,
  five times a second. Each zone learns the levels its light is seen at, unlit and lit, and switches halfway
  between them. Until it has seen both, it is surely lit above 100 (of 255), surely unlit below 40, and
  unknown in between. A state must last a second; four or more changes within 15 s is **blinking**, with how
  often it changes. That is a fault code on many control boards, and on the Pico W a sign that it streams: its
  onboard LED toggles with every delivery, so it blinks every 2 s while readings flow. Draw a light zone tight
  around one LED. The zone counts how often and how long the light was on in the last 24 hours, such as a
  furnace's run cycles.
- **Motion only.** How much of the zone moves; motion events list the zones they touched.

Water reported against a dry floor (red) raises the banner on every view; a "water or stain?" patch (amber)
does not.

**What a floor zone cannot know.** A dark object left on the floor, such as a box or a bag, looks like water
too. Check the picture, then click **Floor is dry**, and the zone learns the floor as it is now, for the lighting
it is in. The camera does not see in the dark: with the lights off, floor zones wait, and a leak is found when
the lights come back on. A $2 water sensor on a spare GPIO pin of the Pico W would raise it at once, in the dark.

**Test it**: draw a floor zone, let it learn the dry floor, pour a little water in it and step out of view. About
a minute later the zone turns red. Wipe the floor, and a minute later it is dry again.

## Recordings

With `CAMERA_RECORD=true` (or `--record`), [`recorder.py`](../src/iotcenter/recorder.py) records a short clip of
each motion event:

- It keeps the last 3 seconds of frames in memory at all times, so a clip starts 3 s before the motion. It ends
  3 s after, and motion that goes on past a minute continues in the next clip.
- Clips are H.264 MP4 at 1280×720 and 10 fps (they play in any browser), with a poster and a small JSON: when,
  how long, the motion's peak and the zones it touched. They are a review copy: the live view keeps the camera's
  full quality. A minute of motion takes a few MB.
- They are kept in `recordings/` next to the database (`data/recordings` locally, `/data/recordings` in the
  `lite-data` volume in Docker), one folder a day. Clips older than `CAMERA_RECORD_DAYS` (30) are deleted every
  hour, and the oldest go sooner if they take more than `CAMERA_RECORD_MAX_GB` (20 GB).

The Camera tab's **Recordings** section shows one day at a time, newest first. A clip plays in a window that also
downloads or deletes it, and a **Recording** mark sits on the live picture while a clip is being recorded.
Recording uses about 10% of one core while it records, and nothing in between.

## Sound

The microphone is read at 16 kHz mono by [`microphone.py`](../src/iotcenter/microphone.py) and analysed by
[`sound.py`](../src/iotcenter/sound.py), 32 ms at a time, 62 times a second:

- **Level** in dBFS, where 0 is the loudest the microphone records, and a background level that follows the room's
  steady noise (a furnace, a fan) over about a minute. **Loud noise**, 20 dB over the background, is an event.
- **Beeps**: a pure tone between 2.5 and 4.5 kHz, where smoke and CO alarms sound, 15 dB over that band's usual
  level. Their rhythm says what is beeping:

| Pattern | Rhythm | Reported as |
|---|---|---|
| Temporal-3 (smoke alarms) | three beeps of about 0.5 s, 0.5 s apart, then a pause | Smoke alarm sounding |
| Temporal-4 (CO alarms) | four beeps of about 0.1 s, 0.1 s apart, then a pause | Carbon monoxide alarm sounding |
| Any other | three or more beeps of one pitch within 6 s | Something is beeping (a leak sensor, a pump alarm) |
| Chirping | one short beep every 20 s to 2 min, at a steady pace | A detector is chirping: low battery? |

An alarm lasts until 10 s after its last beep. While it sounds, every dashboard view shows a red banner.
**Listen** plays the microphone live in the browser, a few seconds behind. Listening and analysing use about
2% of one core. Sound is never recorded: it is analysed and dropped, and only numbers and events stay, in memory.

**Test it** with the test button of a smoke or CO alarm within earshot of the webcam. The Sound card and
Recent activity should show the alarm within about 3 seconds.

## Privacy

- **What is written to disk.** Without recordings, nothing: frames and sound live in memory only, and events
  (times and numbers) are gone after a restart. With recordings, the motion clips, and only them, are kept for
  `CAMERA_RECORD_DAYS` on this machine's disk, then deleted; sound is never recorded. A snapshot is saved by the
  browser on the viewer's device, not on the server. The zones file has names and rectangles.
- **The dashboard has no sign-in of its own,** and with a microphone anyone who can open it can listen. Keep it on
  a network you trust, or put it behind a sign-in: publish it on localhost only (`WEB_PORT=127.0.0.1:18410`) and
  let a reverse proxy with authentication forward to it. Proxies must not buffer the streams: IoT Center sends
  `X-Accel-Buffering: no`, which nginx honours. For other proxies, turn buffering off for
  `/api/camera/stream.mjpg` and `/api/sound/live.wav`.
- **Keep the house out of git.** Clips are in `data/` (git-ignored) or a Docker volume. The camera's `by-id` name
  includes its serial number, so it belongs in the git-ignored `lite/.env`, not in a Compose file. `.gitignore`
  also covers `snapshots/`, `recordings/` and `*.mjpg`. Take screenshots for documentation with the demo scene.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/camera` | state (`starting`, `streaming`, `unavailable`), error, size, format, measured fps, frame size, viewers, and an `activity` summary with the zones and the latest events |
| `GET /api/camera/stream.mjpg?fps=` | the live picture as `multipart/x-mixed-replace` JPEG parts (an `<img>` plays it); `fps` caps the rate |
| `GET /api/camera/snapshot.jpg` | the newest frame at full resolution (`503` while there is none) |
| `GET /api/camera/activity` | the last 10 minutes at a point a second (`t`, `motion_pct`, `brightness_pct`) and the events |
| `GET /api/camera/zones` | every zone (`id`, `name`, `kind`, `x`, `y`, `w`, `h` as fractions of the picture) with what it sees now |
| `POST /api/camera/zones` | a new zone: `{"name", "kind": "floor" \| "drain" \| "light" \| "area", "x", "y", "w", "h", "enabled"}` (`201`) |
| `PATCH /api/camera/zones/{id}` | rename a zone (`name`), or switch it off and on (`enabled`): it keeps what it learned |
| `DELETE /api/camera/zones/{id}` | removes a zone (`204`) |
| `POST /api/camera/zones/{id}/dry` | a floor zone learns the dry floor again |
| `GET /api/camera/recordings?since=&until=` | the motion clips that started between two times, newest first, with the totals and the retention |
| `GET /api/camera/recordings/{id}.mp4`, `.jpg` | a clip (seekable: ranges are supported), and its poster |
| `DELETE /api/camera/recordings/{id}` | deletes a clip |
| `GET /api/sound` | the microphone's state, level, background, any `alarm` or `chirping` now, and the latest sound events |
| `GET /api/sound/activity` | the loudest moment of each second over the last 10 minutes, and the events |
| `GET /api/sound/live.wav` | the live sound as an endless 16 kHz mono WAV stream (an `<audio>` plays it) |
| `GET /api/stream` | also carries a `camera` event per analysed frame (`motion_pct`, `brightness_pct`, `moving`, `boxes`, `light`, `zones`) and a `sound` event four times a second (`level_db`, `background_db`, `beeping`, `alarm`) |

`/api/info` has `"camera": {"name": …, "recording": true}` and `"microphone": true` when they are configured. The Camera tab
appears only with a camera, and the Sound card and Listen only with a microphone. These are Lite features for now.

## Troubleshooting

| Symptom | Check |
|---|---|
| "Camera unavailable: … not found" | Unplugged, or `CAMERA_DEVICE` is wrong: `ls /dev/v4l/by-id`. It comes back by itself within 3 s of plugging in |
| "Camera unavailable: cannot open … in use, or no permission" | Another program has the camera (a video call, `guvcview`, a second IoT Center); a webcam serves one program at a time. Outside Docker, add your user to `video` |
| "Microphone unavailable: audio open error" | Wrong `MICROPHONE_DEVICE` (`arecord -l` lists the cards), the webcam is unplugged, or another program records from it |
| The frame rate drops in the evening | The webcam lengthens its exposure in dim light (UVC `exposure_dynamic_framerate`): a brighter picture at fewer fps |
| The picture stutters from outside the house | Full rate is 27 to 35 Mbit/s at 1080p: pick 5 fps |
| Motion where nothing moves | A flickering light, a screen or a blinking LED is in view. Raise `PIXEL_CHANGE` or `MOTION_START_PCT` in `vision.py`, or draw zones around the parts that matter |
| A floor zone says "possible water", but the floor is dry | Something dark was left there, or a shadow stays. Click **Floor is dry** |
| A floor zone says "water or stain?" about a stain | Click **It's a stain**, or leave it: unchanged for 30 minutes, it becomes part of the floor |
| A zone around a floor drain keeps finding water | Water there is normal: make it a **floor drain** zone, which reports only a pool around it |
| No clips in Recordings | `CAMERA_RECORD=true` in `lite/.env`, and something must move (the Motion card): clips are listed when they end, 3 s after the motion |
| A light zone stays "unknown" or never turns on | The zone's details show its level, and the unlit and lit levels it learned: draw the zone tighter around one light. A light seen only one way (always on) needs a level of 100 to count as lit |
| An alarm is not recognised | Its pitch may be outside 2.5 to 4.5 kHz, or the microphone too far away: the Sound card's level should rise clearly while it beeps |

## Next

These ideas build on the camera and the microphone:

- **Alerts**: once a few weeks of events show the detection can be trusted, push notifications for possible
  water, alarms and chirping, so they reach you away from the dashboard.
- **History**: events stored in SQLite, so they survive a restart, and drawn on the Overview charts next to
  temperature and humidity. A furnace's daily run time is then a chart.
- **People**: a small person detector (an ONNX model through `cv2.dnn`) that runs only while there is motion, so
  it costs nothing while the room is still.
- **Sound signatures**: learn what the furnace, a pump or a fan sounds like, to tell which one is running.
