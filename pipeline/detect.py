import cv2
from ultralytics import YOLO
import sys
import os
from emit import EventEmitter
from tracker import StoreTracker

def process_video(video_path, store_id, camera_id, api_url="http://localhost:8000"):
    print(f"Processing {video_path} for {camera_id}...")
    
    # Load YOLOv8 model (nano version for speed, auto-downloads if missing)
    model = YOLO('yolov8n.pt')
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error opening video {video_path}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    # Initialize our business logic tracker and API emitter
    emitter = EventEmitter(api_url, store_id, camera_id)
    tracker = StoreTracker(camera_id, width, height, fps, emitter)

    frame_count = 0
    while cap.isOpened():
        success, frame = cap.read()
        if not success:
            break
            
        frame_count += 1
        
        # Run YOLOv8 tracking, persisting IDs across frames. 
        # classes=[0] filters for 'person' class only.
        tracker_path = os.path.join(os.path.dirname(__file__), "custom_tracker.yaml")
        results = model.track(frame, persist=True, classes=[0], tracker=tracker_path, verbose=False)
        
        if results and results[0].boxes and results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            track_ids = results[0].boxes.id.int().cpu().tolist()
            confidences = results[0].boxes.conf.cpu().tolist()
            
            # Pass detections to our logic layer
            tracker.update(boxes, track_ids, confidences, frame_count)
            
    cap.release()
    # Flush remaining events
    emitter.flush()
    print(f"Finished processing {frame_count} frames for {camera_id}.")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python detect.py <video_path> <camera_id>")
        sys.exit(1)
        
    vid_path = sys.argv[1]
    cam_id = sys.argv[2]
    # Assuming store is always ST1008 for this challenge dataset
    process_video(vid_path, "ST1008", cam_id)
