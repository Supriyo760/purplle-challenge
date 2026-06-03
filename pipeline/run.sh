#!/bin/bash
echo "Processing all CCTV Footages in the store..."
python3 detect.py "../../CCTV Footage/CAM 1.mp4" "CAM 1"
python3 detect.py "../../CCTV Footage/CAM 2.mp4" "CAM 2"
python3 detect.py "../../CCTV Footage/CAM 3.mp4" "CAM 3"
python3 detect.py "../../CCTV Footage/CAM 4.mp4" "CAM 4"
python3 detect.py "../../CCTV Footage/CAM 5.mp4" "CAM 5"
echo "Pipeline processing complete!"
