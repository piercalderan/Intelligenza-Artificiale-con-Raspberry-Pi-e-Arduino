#!/usr/bin/env python3
from picamera2 import Picamera2, Preview
import cv2, time

# Config videocamera: 640x480 per fluidita su Pi
WIDTH, HEIGHT = 640, 480

picam2 = Picamera2()
video_config = picam2.create_video_configuration(main={"size": (WIDTH, HEIGHT), "format": "RGB888"})
picam2.configure(video_config)
picam2.start()

prev = time.time()
frames = 0
fps = 0.0

print("Premi 'q' sulla finestra per uscire.")
while True:
    frame = picam2.capture_array()  # numpy array RGB
    frames += 1
    now = time.time()
    if now - prev >= 1.0:
        fps = frames / (now - prev)
        prev = now
        frames = 0

    # Mostra FPS
    cv2.putText(frame, f"FPS: {fps:.1f}", (10, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,0), 2, cv2.LINE_AA)

    # OpenCV usa BGR: convertiamo
    bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    cv2.imshow("Anteprima (q per uscire)", bgr)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cv2.destroyAllWindows()
picam2.stop()
