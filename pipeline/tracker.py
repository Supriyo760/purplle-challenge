import math

class StoreTracker:
    ZONE_SKU_MAP = {
        "SKINCARE": "MOISTURISER",
        "MAKEUP": "FOUNDATION",
        "BILLING": "BILLING_COUNTER"
    }

    def __init__(self, camera_id, width, height, fps, emitter):
        self.camera_id = camera_id
        self.width = width
        self.height = height
        self.fps = fps
        self.emitter = emitter
        
        # State tracking
        self.visitors = {} # track_id -> dict of state
        self.exited_ids = {} # track_id -> exit_frame (for REENTRY detection)
        self.session_counters = {} # visitor_id -> running event counter
        
        # Zone definitions based on frame percentages
        self.zones = {
            "SKINCARE": {"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 0.5},
            "MAKEUP": {"x1": 0.0, "y1": 0.5, "x2": 1.0, "y2": 1.0},
            "BILLING": {"x1": 0.7, "y1": 0.7, "x2": 1.0, "y2": 1.0}
        }
        
        self.avg_person_area_ratio = 0.04 # 4% of frame area

        # Staff detection: track how long each track_id is visible.
        self.total_frames_processed = 0
        self.staff_frame_threshold_pct = 0.6  # 60% presence = staff
        
        # --- Filtering thresholds ---
        # Motion: must move at least 5% of frame diagonal to be real
        self.min_motion_pct = 0.05
        self.min_motion_dist = math.hypot(width, height) * self.min_motion_pct
        
        # Minimum bounding box area: real person must be at least 1.5% of frame
        self.min_bbox_area_ratio = 0.015
        
        # Minimum track duration: must be seen for at least 1.5 seconds
        self.min_track_frames = int(fps * 1.5)
        
        # Stale threshold: match the track_buffer (30 seconds)
        self.stale_threshold = int(30 * fps)

    def _next_seq(self, visitor_id):
        """Return and increment session_seq for a visitor."""
        self.session_counters.setdefault(visitor_id, 0)
        self.session_counters[visitor_id] += 1
        return self.session_counters[visitor_id]

    def get_zone(self, x_center, y_center):
        nx = x_center / self.width
        ny = y_center / self.height
        
        # Billing overrides Makeup if in the bottom right corner
        if self.zones["BILLING"]["x1"] <= nx <= self.zones["BILLING"]["x2"] and self.zones["BILLING"]["y1"] <= ny <= self.zones["BILLING"]["y2"]:
            return "BILLING"
            
        if self.zones["SKINCARE"]["y1"] <= ny <= self.zones["SKINCARE"]["y2"]:
            return "SKINCARE"
            
        return "MAKEUP"
        
    def estimate_person_count(self, bbox):
        # Disabled the area heuristic. Since cameras are mounted low, 
        # a single person close to the camera can take up 20% of the frame,
        # falsely inflating the visitor count.
        return 1

    def _check_staff(self, track_id):
        """Heuristic: if a person has been visible for >60% of all frames
        processed so far, they are likely store staff (cashier, floor attendant)."""
        if self.total_frames_processed < 100:
            return False  # not enough data to judge
        v = self.visitors.get(track_id)
        if not v:
            return False
        frames_visible = v.get("frames_visible", 0)
        return (frames_visible / self.total_frames_processed) > self.staff_frame_threshold_pct

    def _is_bbox_too_small(self, box):
        """Filter out tiny detections (faces on product packaging, distant reflections)."""
        x1, y1, x2, y2 = box
        box_area = (x2 - x1) * (y2 - y1)
        frame_area = self.width * self.height
        return (box_area / frame_area) < self.min_bbox_area_ratio

    def _validate_person(self, v, xc, yc):
        """Check if this tracked object has moved enough to be a real person.
        Returns True if the person has just been validated (first time)."""
        if v.get("is_valid_person", False):
            return False  # already validated
        
        ix, iy = v["initial_pos"]
        distance = math.hypot(xc - ix, yc - iy)
        
        # Must have moved enough AND been tracked for enough frames
        frames_visible = v.get("frames_visible", 0)
        if distance > self.min_motion_dist and frames_visible >= self.min_track_frames:
            v["is_valid_person"] = True
            return True
        
        return False

    def _emit_initial_events(self, track_id, v, conf, current_zone):
        """Emit the delayed ENTRY + ZONE_ENTER events when a person is first validated."""
        pc = v.get("person_count", 1)
        is_staff = v.get("is_staff", False)
        is_reentry = v.get("is_reentry", False)
        
        # Emit ENTRY or REENTRY if it's the entry camera
        if self.camera_id in ["CAM 1", "CAM 1.mp4", "CAM_ENTRY_01"]:
            event_type = "REENTRY" if is_reentry else "ENTRY"
            vid = str(track_id)
            emit_conf = conf
            seq = self._next_seq(vid)
            self.emitter.emit(vid, event_type, confidence=emit_conf, is_staff=is_staff,
                              metadata={"group_size": pc, "session_seq": seq})
            v["has_entered"] = True
            
            if is_reentry and track_id in self.exited_ids:
                del self.exited_ids[track_id]
        
        # Emit ZONE_ENTER
        vid = str(track_id)
        sku = self.ZONE_SKU_MAP.get(current_zone)
        seq = self._next_seq(vid)
        if current_zone == "BILLING":
            self.emitter.emit(vid, "BILLING_QUEUE_JOIN", zone_id="BILLING",
                              metadata={"queue_depth": 1, "group_size": pc,
                                        "sku_zone": sku, "session_seq": seq},
                              confidence=conf)
        else:
            self.emitter.emit(vid, "ZONE_ENTER", zone_id=current_zone, confidence=conf,
                              metadata={"group_size": pc, "sku_zone": sku, "session_seq": seq})

    def update(self, boxes, track_ids, confidences, frame_count):
        self.total_frames_processed = frame_count
        active_ids = set()
        
        for box, track_id, conf in zip(boxes, track_ids, confidences):
            active_ids.add(track_id)
            
            # Filter 1: Skip tiny bounding boxes (product packaging faces, distant reflections)
            if self._is_bbox_too_small(box):
                continue
            
            x1, y1, x2, y2 = box
            xc = (x1 + x2) / 2
            yc = (y1 + y2) / 2
            
            current_zone = self.get_zone(xc, yc)
            
            if track_id not in self.visitors:
                person_count = self.estimate_person_count(box)
                
                # Check if this is a REENTRY (previously exited)
                is_reentry = track_id in self.exited_ids
                
                self.visitors[track_id] = {
                    "first_frame": frame_count,
                    "initial_pos": (xc, yc),
                    "is_valid_person": False,  # Must move to be considered real
                    "zone": current_zone,
                    "zone_start_frame": frame_count,
                    "is_staff": False,
                    "last_dwell_emit": frame_count,
                    "has_entered": False,
                    "person_count": person_count,
                    "frames_visible": 1,
                    "is_reentry": is_reentry
                }
            else:
                v = self.visitors[track_id]
                pc = v.get("person_count", 1)
                v["frames_visible"] = v.get("frames_visible", 0) + 1
                
                # Dynamically update staff flag
                is_staff = self._check_staff(track_id)
                v["is_staff"] = is_staff
                
                # Filter 2: Validate real person (motion + duration check)
                just_validated = self._validate_person(v, xc, yc)
                if just_validated:
                    self._emit_initial_events(track_id, v, conf, current_zone)
                
                # If they haven't been validated yet, skip emitting any further events
                if not v.get("is_valid_person", False):
                    # Still update last_seen so we don't prematurely clean up
                    v["last_seen"] = frame_count
                    continue
                
                # Check zone change
                if v["zone"] != current_zone:
                    vid = str(track_id)
                    # Emit ZONE_EXIT for old zone
                    seq = self._next_seq(vid)
                    if v["zone"] == "BILLING":
                        self.emitter.emit(vid, "BILLING_QUEUE_ABANDON", zone_id="BILLING",
                                          confidence=conf, is_staff=is_staff,
                                          metadata={"session_seq": seq})
                    else:
                        self.emitter.emit(vid, "ZONE_EXIT", zone_id=v["zone"], confidence=conf,
                                          is_staff=is_staff, metadata={"session_seq": seq})
                    
                    # Enter new zone
                    sku = self.ZONE_SKU_MAP.get(current_zone)
                    seq = self._next_seq(vid)
                    if current_zone == "BILLING":
                        self.emitter.emit(vid, "BILLING_QUEUE_JOIN", zone_id="BILLING",
                                          metadata={"queue_depth": 1, "group_size": pc,
                                                    "sku_zone": sku, "session_seq": seq},
                                          confidence=conf, is_staff=is_staff)
                    else:
                        self.emitter.emit(vid, "ZONE_ENTER", zone_id=current_zone,
                                          confidence=conf, is_staff=is_staff,
                                          metadata={"group_size": pc, "sku_zone": sku, "session_seq": seq})
                        
                    v["zone"] = current_zone
                    v["zone_start_frame"] = frame_count
                    v["last_dwell_emit"] = frame_count
                
                # Check dwell time (every 30 seconds = 30 * fps frames)
                frames_in_zone = frame_count - v["zone_start_frame"]
                frames_since_last_emit = frame_count - v["last_dwell_emit"]
                
                dwell_threshold = 30 * self.fps
                if frames_in_zone >= dwell_threshold and frames_since_last_emit >= dwell_threshold:
                    dwell_ms = int((frames_in_zone / self.fps) * 1000)
                    sku = self.ZONE_SKU_MAP.get(current_zone)
                    vid = str(track_id)
                    seq = self._next_seq(vid)
                    self.emitter.emit(vid, "ZONE_DWELL", zone_id=current_zone,
                                      dwell_ms=dwell_ms, confidence=conf,
                                      is_staff=is_staff,
                                      metadata={"sku_zone": sku, "session_seq": seq})
                    v["last_dwell_emit"] = frame_count
                    
            # Record last seen frame for cleanup
            self.visitors[track_id]["last_seen"] = frame_count
                    
        # Cleanup stale track IDs to prevent memory leak
        # Matches track_buffer (30 seconds) so we don't kill IDs that the tracker still remembers
        stale_ids = []
        for tid, v in self.visitors.items():
            if frame_count - v.get("last_seen", frame_count) > self.stale_threshold:
                stale_ids.append(tid)
                
        for tid in stale_ids:
            v = self.visitors[tid]
            # Only emit exit events for validated persons
            if not v.get("is_valid_person", False):
                del self.visitors[tid]
                continue
                
            is_staff = v.get("is_staff", False)
            vid = str(tid)
            seq = self._next_seq(vid)
            if self.camera_id in ["CAM 1", "CAM 1.mp4", "CAM_ENTRY_01"]:
                self.emitter.emit(vid, "EXIT", is_staff=is_staff,
                                  metadata={"session_seq": seq})
                self.exited_ids[tid] = frame_count  # remember for REENTRY
            elif v.get("zone") == "BILLING":
                self.emitter.emit(vid, "BILLING_QUEUE_ABANDON", zone_id="BILLING",
                                  is_staff=is_staff, metadata={"session_seq": seq})
            else:
                self.emitter.emit(vid, "ZONE_EXIT", zone_id=v.get("zone"),
                                  is_staff=is_staff, metadata={"session_seq": seq})
                
            del self.visitors[tid]
