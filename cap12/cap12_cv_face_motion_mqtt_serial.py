#!/usr/bin/env python3
from picamera2 import Picamera2
import cv2, numpy as np, time, os
#from alerts import AlertSink

# ===== Parametri videocamera e detection =====
WIDTH, HEIGHT = 640, 480
SCALE_FACTOR = 1.2
MIN_NEIGHBORS = 5
MIN_SIZE = (60, 60)
MOTION_THRESH = 25
MOTION_MIN_AREA = 900

# ===== Percorsi Haar Cascade =====
##CANDIDATES = [
##    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml",
##    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
##]

cascade_path = "haarcascade_frontalface_default.xml"
if not cascade_path:
    raise SystemExit("Haar cascade non trovato.")
face_cascade = cv2.CascadeClassifier(cascade_path)

# ===== Sink: configura qui MQTT e Serial =====
##sink = AlertSink(
##    mqtt_host="localhost",
##    mqtt_port=1883,
##    mqtt_topic="home/cam/alerts",
##    client_id=None,
##    enable_mqtt=True,
##    serial_port="/dev/ttyACM0",   # metti qui la tua porta o None per disattivare
##    serial_baud=115200,
##    enable_serial=True
##)

# ===== Stato per debouncing =====
motion_active = False
calm_frames = 0
CALM_FRAMES_TO_STOP = 8        # quanti frame "calmi" prima di inviare motion_end
LAST_FACE_EVENT = 0.0
FACE_EVENT_COOLDOWN = 3.0      # invia un evento "face" al massimo ogni 3s

try:
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
        face_count = 0
        for (x, y, w, h) in faces:
            face_count += 1
            cv2.rectangle(frame, (x,y), (x+w, y+h), (0,255,0), 2)
            cv2.putText(frame, "Face", (x, y-8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

        # invia evento "face" rate-limited
        now = time.time()
        if face_count > 0 and (now - LAST_FACE_EVENT) >= FACE_EVENT_COOLDOWN:
            #sink.send_event("face", {"faces": face_count}, cooldown_s=0.0)  # cooldown gestito sopra
            LAST_FACE_EVENT = now

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

        # stato e invio eventi
        if motion_detected:
            cv2.putText(frame, "MOTION", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255,0,0), 2)
            calm_frames = 0
            if not motion_active:
                motion_active = True
                #sink.send_event("motion_start", {"note": "movement detected"}, cooldown_s=0.0)
            else:
                # keepalive ogni 2s (gestito dal cooldown del sink)
                #sink.send_event("motion_keepalive", {"note": "still moving"}, cooldown_s=2.0)
                pass
        else:
            if motion_active:
                calm_frames += 1
                if calm_frames >= CALM_FRAMES_TO_STOP:
                    motion_active = False
                    calm_frames = 0
                    #sink.send_event("motion_end", {"note": "movement stopped"}, cooldown_s=0.0)

        prev_gray = gray_blur

        cv2.imshow("Face + Motion + MQTT/Serial (q per uscire)", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    try:
        cv2.destroyAllWindows()
    except:
        pass
    try:
        picam2.stop()
    except:
        pass
    try:
        sink.close()
    except:
        pass
