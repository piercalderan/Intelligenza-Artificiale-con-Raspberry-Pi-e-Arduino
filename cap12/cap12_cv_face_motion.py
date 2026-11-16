#!/usr/bin/env python3
from picamera2 import Picamera2
import cv2, numpy as np, time, os

# Parametri
WIDTH, HEIGHT = 640, 480
SCALE_FACTOR = 1.2    # per face detection
MIN_NEIGHBORS = 5
MIN_SIZE = (60, 60)   # volti almeno 60x60
MOTION_THRESH = 25    # sensibilita del movimento
MOTION_MIN_AREA = 900 # area minima per considerare 'movimento'

# Carica il classificatore Haar (incluso in OpenCV)
# Su molte installazioni: /usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml
# Fallback: cv2.data.haarcascades
CANDIDATES = [
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
]
cascade_path = next((p for p in CANDIDATES if os.path.exists(p)), None)
if not cascade_path:
    raise SystemExit("Haar cascade non trovato. Installa/opencv-data o aggiorna i path.")
face_cascade = cv2.CascadeClassifier(cascade_path)

# Camera
picam2 = Picamera2()
config = picam2.create_video_configuration(main={"size": (WIDTH, HEIGHT), "format": "RGB888"})
picam2.configure(config)
picam2.start()
time.sleep(0.2)

print("Premi 'q' per uscire.")
prev_gray = None
while True:
    rgb = picam2.capture_array()
    frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray_blur = cv2.GaussianBlur(gray, (5,5), 0)

    # ---- Face detection ----
    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=SCALE_FACTOR,
        minNeighbors=MIN_NEIGHBORS,
        minSize=MIN_SIZE
    )
    for (x, y, w, h) in faces:
        cv2.rectangle(frame, (x,y), (x+w, y+h), (0,255,0), 2)
        cv2.putText(frame, "Face", (x, y-8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

    # ---- Motion detection ----
    motion_detected = False
    if prev_gray is not None:
        delta = cv2.absdiff(prev_gray, gray_blur)
        thresh = cv2.threshold(delta, MOTION_THRESH, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for c in contours:
            if cv2.contourArea(c) < MOTION_MIN_AREA:
                continue
            (mx, my, mw, mh) = cv2.boundingRect(c)
            cv2.rectangle(frame, (mx,my), (mx+mw, my+mh), (255,0,0), 2)
            motion_detected = True

    if motion_detected:
        cv2.putText(frame, "MOTION", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255,0,0), 2)

    prev_gray = gray_blur

    cv2.imshow("Face + Motion (q per uscire)", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cv2.destroyAllWindows()
picam2.stop()
