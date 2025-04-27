#!/usr/bin/env python
# -*- coding: utf-8 -*-

# === Python 2.7 NAO Gemini Controller (Restructured) ===
# Uses REST API for Gemini interaction.

import sys
import time
import json
import urllib2 # Use urllib2 for HTTP in Python 2.7
import base64  # For encoding images
import io
import os
import math
import argparse
import traceback # For detailed error printing

# Attempt to import NAOqi SDK
try:
    from naoqi import ALProxy
    import vision_definitions # Contains constants like kRGBColorSpace, kQVGA, etc.
    import motion # Contains constants like FRAME_ROBOT
except ImportError:
    print "--------------------------------------------------"
    print " Error: NAOqi SDK not found or not in PYTHONPATH."
    print "        Please install it for your system."
    print "--------------------------------------------------"
    sys.exit(1)

# Attempt to import Pillow
try:
    from PIL import Image
except ImportError:
    print "--------------------------------------------------"
    print " Error: Pillow (PIL) not found."
    print "        Please install it (e.g., pip install Pillow==6.2.2)."
    print "--------------------------------------------------"
    sys.exit(1)


# ========================================
# ====        CONFIGURATION           ====
# ========================================

# -- NAO Connection --
NAO_IP_DEFAULT = "169.254.79.239" # Default IP from your example
NAO_PORT = 9559

# -- Gemini API --
GEMINI_API_KEY = "AIzaSyBRPzDg8HrjvIXY9sryD40onS5JeUwvHSA" # !!! REPLACE THIS !!!
# Using v1beta as it includes vision capabilities compatible with API Keys
GEMINI_REST_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models/"
# Using 1.5 Flash as it's generally fast and capable
GEMINI_TEXT_MODEL = "gemini-1.5-flash-latest"
GEMINI_VISION_MODEL = "gemini-1.5-flash-latest" # Can also try "gemini-pro-vision"

# -- Camera Settings --
CAMERA_ID = 1  # 0: Top camera, 1: Bottom camera
RESOLUTION = vision_definitions.kQVGA # 320x240 - Lower res is faster
COLOR_SPACE = vision_definitions.kRGBColorSpace # Use RGB
FPS = 5 # Keep FPS low for this application

# -- Motion Settings --
MOVE_SPEED_DEFAULT = 0.15 # Fraction of maximum speed (0.05-1.0). START SLOW!
HEAD_MOVEMENT_SPEED = 0.15 # Speed for head movements
HEAD_PITCH_LOOK_DOWN = 0.5 # Radians (~28 degrees down) - Adjust as needed
DEFAULT_ARM = "RArm" # Which arm to use ('LArm' or 'RArm')
DEFAULT_HAND = "RHand" # Which hand ('LHand' or 'RHand')
STIFFNESS_ON = 0.85 # Stiffness for movement

# -- Robot Frames / Constants --
FRAME = motion.FRAME_ROBOT
AXIS_MASK_ALL = 7 # Mask for position and orientation

# --- File Paths ---
IMAGE_CAPTURE_FILENAME = "nao_capture.png"
IMAGE_EVAL_FILENAME = "nao_eval.png"


# ========================================
# ====    NAOQI HELPER FUNCTIONS      ====
# ========================================
# (Functions for specific NAOqi actions - largely unchanged internally)

def execute_open_hand(motion_proxy, hand_name):
    """Opens the specified hand."""
    print "Opening {}...".format(hand_name)
    try:
        motion_proxy.openHand(hand_name)
        print "{} opened.".format(hand_name)
        return True
    except Exception, e:
        print "Error opening hand {}: {}".format(hand_name, e)
        return False

def execute_close_hand(motion_proxy, hand_name):
    """Closes the specified hand."""
    print "Closing {}...".format(hand_name)
    try:
        motion_proxy.closeHand(hand_name)
        print "{} closed.".format(hand_name)
        return True
    except Exception, e:
        print "Error closing hand {}: {}".format(hand_name, e)
        return False

def execute_move_arm_to_coords(motion_proxy, target_coords, arm_name, speed):
    """Moves the arm end-effector to target coordinates."""
    print "Moving {} to [{:.3f}, {:.3f}, {:.3f}] at speed {:.2f}...".format(arm_name, target_coords[0], target_coords[1], target_coords[2], speed)
    try:
        if not isinstance(target_coords, list) or len(target_coords) != 3:
             print "Error: Target coordinates must be a list of 3 elements. Got: {}".format(target_coords)
             return False
        target_orientation = [0.0, math.radians(20.0), 0.0] # Default orientation
        full_target = target_coords + target_orientation
        motion_proxy.setPositions(arm_name, FRAME, full_target, speed, AXIS_MASK_ALL)
        motion_proxy.waitUntilMoveIsFinished() # Blocking wait
        print "{} move complete.".format(arm_name)
        return True
    except Exception, e:
        print "Error moving arm {} to coords: {}".format(arm_name, e)
        return False

def execute_move_arm_relative(motion_proxy, offset, arm_name, speed):
     """Moves the arm by a relative offset [dx, dy, dz]."""
     print "Moving {} relatively by [{:.3f}, {:.3f}, {:.3f}]...".format(arm_name, offset[0], offset[1], offset[2])
     try:
          current_pos = motion_proxy.getPosition(arm_name, FRAME, True) # Use sensors
          if len(current_pos) < 3:
               print "Error: Could not get current arm position."
               return False
          target_coords = [current_pos[i] + offset[i] for i in range(3)]
          return execute_move_arm_to_coords(motion_proxy, target_coords, arm_name, speed)
     except Exception, e:
          print "Error moving arm {} relatively: {}".format(arm_name, e)
          return False

# ========================================
# ==== IMAGE & COORD HELPER FUNCTIONS ====
# ========================================

def capture_image_nao(video_proxy, filename):
    """Captures an image from NAO's camera and saves it."""
    print "Capturing image from camera ID {}...".format(CAMERA_ID)
    subscriber_id = "pyclient_{}".format(int(time.time()))
    video_client = None
    try:
        video_client = video_proxy.subscribeCamera(
            subscriber_id, CAMERA_ID, RESOLUTION, COLOR_SPACE, FPS)
        if not video_client: print "Error: Cannot subscribe."; return None, None, None # Removed bytes return
        time.sleep(0.3)
        nao_image = video_proxy.getImageRemote(video_client)
        video_proxy.unsubscribe(video_client); video_client = None

        if nao_image is None or len(nao_image) < 7: print "Error: Cannot retrieve image."; return None, None, None
        w, h, layers, img_data = nao_image[0], nao_image[1], nao_image[2], str(nao_image[6])
        if layers != 3: print "Warning: Expected 3 color layers, got {}".format(layers)

        img = Image.frombytes("RGB", (w, h), img_data)
        img.save(filename)
        print "Image saved as {} ({}x{})".format(filename, w, h)
        # Return path, width, height
        return filename, w, h
    except Exception, e:
        print "Error capturing image: {}".format(e)
        if video_client:
            try: video_proxy.unsubscribe(video_client)
            except: pass
        return None, None, None # Return None for all on error

def pixels_to_world_estimation(box_center_px, image_dims_px, motion_proxy):
    """ ESTIMATES world coordinates (FRAME_ROBOT). NEEDS CALIBRATION! """
    # (Using the same highly approximate logic - requires calibration)
    print "Estimating world coords for pixel: {}".format(box_center_px)
    x_px, y_px = box_center_px
    width_px, height_px = image_dims_px
    if not width_px or not height_px:
        print "Error: Invalid image dimensions"
        return None # Avoid division by zero
    norm_x = (float(x_px) / width_px) - 0.5
    norm_y = (float(y_px) / height_px) - 0.5
    try:
        cam_angles = motion_proxy.getAngles(["HeadYaw", "HeadPitch"], False)
        head_yaw, head_pitch = cam_angles
        fov_h_rad = math.radians(60.9); fov_v_rad = math.radians(47.6)
        angle_x_offset = -norm_x * fov_h_rad
        final_angle_x = head_yaw + angle_x_offset
        # Simplified fixed distance projection
        est_dist_x = 0.35; target_z = 0.0
        est_dist_y = est_dist_x * math.tan(final_angle_x)
        estimated_coords = [est_dist_x, est_dist_y, target_z]
        print "  -> Estimated Coords (FRAME_ROBOT): [{:.3f}, {:.3f}, {:.3f}]".format(estimated_coords[0], estimated_coords[1], estimated_coords[2])
        # Sanity check/clamp
        if not (0.1 < estimated_coords[0] < 0.6 and abs(estimated_coords[1]) < 0.4):
            print "Warning: Estimated coords unreasonable. Clamping."
            estimated_coords[0] = max(0.15, min(0.45, estimated_coords[0]))
            estimated_coords[1] = max(-0.3, min(0.3, estimated_coords[1]))
            print "  -> Clamped Coords: [{:.3f}, {:.3f}, {:.3f}]".format(estimated_coords[0], estimated_coords[1], estimated_coords[2])
        return estimated_coords
    except Exception, e:
        print "Error during pixel_to_world estimation: {}".format(e)
        return None

# ========================================
# ====    GEMINI HELPER FUNCTIONS     ====
# ========================================

def call_gemini_rest_api(model_name, payload, api_key):
    """ Calls Gemini REST API using urllib2. Returns parsed JSON or None. """
    print "\n--- Calling Gemini REST API ({}) ---".format(model_name)
    url = "{}{}:generateContent?key={}".format(GEMINI_REST_API_BASE, model_name, api_key)
    json_payload = json.dumps(payload)
    try:
        request = urllib2.Request(url, data=json_payload, headers={'Content-Type': 'application/json'})
        response = urllib2.urlopen(request, timeout=90) # Increased timeout
        response_body = response.read()
        response_code = response.getcode()
        print "Gemini API Response Code: {}".format(response_code)
        if response_code == 200:
            try:
                parsed_json = json.loads(response_body)
                if not parsed_json or not parsed_json.get('candidates'):
                    print "Error: Gemini response missing 'candidates'."
                    print "Full Response: {}".format(parsed_json)
                    reason = None
                    if parsed_json and parsed_json.get('promptFeedback', {}).get('blockReason'):
                        reason = parsed_json['promptFeedback']['blockReason']
                    elif parsed_json and parsed_json.get('error'):
                        reason = parsed_json.get('error')
                    if reason:
                        print "API Block/Error Reason: {}".format(reason)
                    return None
                return parsed_json
            except ValueError, e: # JSON parse error
                print "Error parsing JSON from Gemini: {}".format(e); print "Received: {}".format(response_body); return None
        else: print "Error: Gemini API failed status {}. Resp: {}".format(response_code, response_body); return None
    except urllib2.HTTPError, e:
        print "HTTP Error calling Gemini: {} {}".format(e.code, e.reason)
        try:
            print "Error Body: {}".format(e.read())
        except:
            pass
        return None

    except urllib2.URLError, e: print "URL Error calling Gemini: {}".format(e.reason); return None
    except Exception, e: print "Unexpected Error calling Gemini API: {}".format(e); traceback.print_exc(); return None

def call_gemini_text_rest(prompt):
    """Sends text prompt to Gemini REST API."""
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    response_json = call_gemini_rest_api(GEMINI_TEXT_MODEL, payload, GEMINI_API_KEY)
    if response_json and response_json.get('candidates'):
        try: return response_json['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError, TypeError), e: print "Error parsing text from Gemini response: {}".format(e); print response_json; return None
    return None

def call_gemini_vision_rest(prompt, image_bytes, mime_type="image/png"):
    """Sends image (bytes) and prompt to Gemini Vision REST API."""
    try: base64_image = base64.b64encode(image_bytes)
    except Exception, e: print "Error Base64 encoding image: {}".format(e); return None
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": base64_image}}]}]}
    response_json = call_gemini_rest_api(GEMINI_VISION_MODEL, payload, GEMINI_API_KEY)
    if response_json and response_json.get('candidates'):
        try: return response_json['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError, TypeError), e: print "Error parsing text from Gemini vision response: {}".format(e); print response_json; return None
    return None

def parse_json_from_gemini(response_text):
    """Attempts to parse JSON from Gemini's response, cleaning markdown."""
    if not response_text: return None
    try:
        cleaned_text = response_text.strip()
        if cleaned_text.startswith("```json"): cleaned_text = cleaned_text[7:]
        if cleaned_text.endswith("```"): cleaned_text = cleaned_text[:-3]
        cleaned_text = cleaned_text.strip()
        if not cleaned_text: print "Warning: Cleaned Gemini response is empty."; return None
        return json.loads(cleaned_text)
    except ValueError, e: print "Error parsing JSON: {}".format(e); print "Received text:\n---\n{}\n---".format(response_text); return None
    except Exception, e: print "Unexpected error during JSON parsing: {}".format(e); return None

# ========================================
# ====    MAIN CONTROLLER CLASS       ====
# ========================================

class NAOGeminiController:
    """ Encapsulates the state and logic for the NAO task execution. """
    def __init__(self, nao_proxies, move_speed_param):
        """ Initialize controller with NAO proxies and config. """
        if not nao_proxies:
            raise ValueError("NAO proxies dictionary cannot be None")
        self.proxies = nao_proxies
        self.motion = self.proxies['ALMotion']
        self.video = self.proxies['ALVideoDevice']
        self.tts = self.proxies['ALTextToSpeech']
        # self.posture = self.proxies['ALRobotPosture'] # Might not be needed directly in class

        self.world_model = {} # Stores {'object_label': [x, y, z]}
        self.current_image_path = None
        self.image_dims = (None, None) # width, height
        self.move_speed = move_speed_param

        # Arm/Hand defaults from config
        self.arm = DEFAULT_ARM
        self.hand = DEFAULT_HAND

        # Pre-defined positions/offsets from config
        self.pre_grasp_offset = [-0.03, 0.0, 0.08] # Slightly back, centered, and above target
        self.retract_position = [0.15, 0.0, 0.25] # Simplified neutral pose
        self.lift_height = 0.08

    def say(self, text, blocking=False):
        """Make NAO speak."""
        try:
            safe_text = str(text) # Ensure basic string
            if blocking: self.tts.say(safe_text)
            else: self.tts.post.say(safe_text) # Non-blocking
        except Exception, e:
            print "TTS Error: {}".format(e)

    def update_perception(self):
        """Capture image, detect objects via REST API, estimate coords."""
        self.say("Let me take a look.", blocking=True)
        # try:
        #      self.motion.angleInterpolationWithSpeed(["HeadYaw", "HeadPitch"], [0.0, 0.0], 0.1)
        #      time.sleep(1.5)
        # except Exception, e: print "Warning: Could not set head angle - {}".format(e)

        # Capture Image - Don't need img_bytes directly anymore
        self.current_image_path, w, h = capture_image_nao(self.video, IMAGE_CAPTURE_FILENAME)
        if not self.current_image_path:
            self.say("I couldn't see clearly.")
            return False
        self.image_dims = (w, h)

        # --- Read the *saved* PNG file for API ---
        img_bytes_for_api = None
        try:
            with open(self.current_image_path, 'rb') as f: # Open in binary read mode
                img_bytes_for_api = f.read()
            print "Read {} bytes from saved PNG file.".format(len(img_bytes_for_api))
        except IOError, e:
            print "Error reading saved image file {}: {}".format(self.current_image_path, e)
            self.say("I had trouble reading the picture I took.")
            return False
        except Exception, e: # Catch other potential errors
             print "Unexpected error reading image file: {}".format(e)
             self.say("An unexpected error occurred reading the picture.")
             return False

        if not img_bytes_for_api: # Check if reading failed or file was empty
             print "Error: Failed to get image bytes for API."
             return False
        # --- End Read File ---


        # --- Build Gemini Vision Prompt Safely (Keep the safe building logic) ---
        prompt_template = """Analyze the attached image ({}x{} pixels)...(same as before)"""
        example_json_string = """Example: [{{"label": "red can", "box": [100, 150, 180, 300]}}]"""
        closing_instructions = """If no relevant objects...(same as before)"""
        formatted_part = prompt_template.format(w, h, w-1, h-1)
        prompt = "\n".join([formatted_part, example_json_string, closing_instructions])
        # --- End Safe Prompt Building ---

        # --- Call API with CORRECT bytes ---
        vision_response_text = call_gemini_vision_rest(prompt, img_bytes_for_api) # Use bytes from file
        detected_objects = parse_json_from_gemini(vision_response_text)

        # ... (rest of the perception update logic remains the same) ...
        # ... (Make sure the rest of the function is identical to the previous working version) ...
        if detected_objects is None:
            self.say("I had trouble understanding what I was looking at.")
            return False
        self.world_model = {}
        valid_detections = 0
        if not detected_objects:
            self.say("I don't see any objects I recognize right now.")
            return True # Success, but no objects found
        print "Processing detected objects:"
        for obj in detected_objects:
            if isinstance(obj, dict) and 'label' in obj and 'box' in obj:
                label = obj['label']; box = obj['box']
                if not (isinstance(box, list) and len(box) == 4 and all(isinstance(n, int) for n in box)): print "  - Skipping '{}': Invalid box format {}".format(label, box); continue
                if not (0 <= box[0] < box[2] < w and 0 <= box[1] < box[3] < h): print "  - Skipping '{}': Box out of bounds {} in ({}x{})".format(label, box, w, h); continue
                center_x = (box[0] + box[2]) / 2.0; center_y = (box[1] + box[3]) / 2.0
                coords = pixels_to_world_estimation((center_x, center_y), self.image_dims, self.motion)
                if coords:
                    self.world_model[label] = coords
                    print "  + Found '{}' at estimated coords [{:.3f}, {:.3f}, {:.3f}]".format(label, coords[0], coords[1], coords[2])
                    valid_detections += 1
                else: print "  - Skipping '{}': Failed coordinate estimation.".format(label)
            else: print "Warning: Skipping malformed object entry: {}".format(obj)
        if valid_detections > 0: self.say("Okay, I see {} object{}.".format(valid_detections, 's' if valid_detections > 1 else ''))
        else: self.say("I couldn't pinpoint any objects.")
        print "\n--- World Model Updated ---"
        if self.world_model:
            for label, coords in self.world_model.iteritems(): print "- {}: [{:.3f}, {:.3f}, {:.3f}]".format(label, coords[0], coords[1], coords[2])
        else: print "No objects with valid coordinates in model."
        print "---------------------------\n"
        return True

    def plan_task(self, goal):
        """Use Gemini Text REST API to generate plan."""
        if not self.world_model:
             self.say("I haven't seen any objects yet, so I can't plan."); return None
        self.say("Okay, planning how to achieve: {}".format(goal), blocking=True)

        # Construct the planning prompt (use the detailed one)
        prompt = """
        You are a methodical planning assistant for a NAO humanoid robot.
        Goal: "{}"
        Current World State (estimated object coordinates [x, y, z] in meters relative to robot base):
        {}
        Robot Resources: Arm: {}, Hand: {} (starts open)
        Generate a sequence of specific, executable steps for the NAO robot.
        Use ONLY the following action verbs: IDENTIFY <label>, OPEN_HAND, REACH_PRE_GRASP <label>, REACH_GRASP <label>, CLOSE_HAND, LIFT, MOVE_ARM_TO_COORDS <[x,y,z]>, MOVE_ARM_RELATIVE <[dx,dy,dz]>, RELEASE, RETRACT_ARM.
        Constraints & Logic: (Always OPEN_HAND before grasp, REACH_PRE_GRASP before REACH_GRASP, LIFT after CLOSE_HAND, RELEASE after move, RETRACT_ARM after release. Handle relative placements if possible.)
        Output ONLY as a numbered list. No explanations.
        """.format(goal, json.dumps(self.world_model, indent=2) if self.world_model else "No objects detected.", self.arm, self.hand)

        plan_response_text = call_gemini_text_rest(prompt)
        if not plan_response_text:
            self.say("I couldn't come up with a plan."); return None

        # Parse plan (same robust parsing)
        try: # Wrap parsing in try/except
            plan = []
            raw_lines = plan_response_text.strip().split('\n')
            for line in raw_lines:
                line_strip = line.strip()
                if line_strip and line_strip[0].isdigit():
                    first_space_index = -1
                    for i, char in enumerate(line_strip):
                        if char == ' ' and i > 0:
                             is_prefix_num = all(line_strip[j].isdigit() or line_strip[j] == '.' for j in xrange(i))
                             if is_prefix_num: first_space_index = i; break
                    if first_space_index != -1:
                        action = line_strip[first_space_index+1:].strip()
                        if action: plan.append(action)
            if not plan and raw_lines: # Fallback
                 print "Warning: Could not parse numbered list, using raw lines."
                 plan = [line.strip() for line in raw_lines if line.strip()]
        except Exception, e:
             print "Error parsing plan: {}".format(e); print "Raw response:\n{}".format(plan_response_text); self.say("My planning thoughts got scrambled."); return None
        if not plan: print "Warning: Could not extract plan."; self.say("I couldn't make sense of the plan."); return None

        self.say("Alright, I have a {} step plan.".format(len(plan)))
        print "\n--- Generated Plan ---"; # (print plan) ... ; print "----------------------\n"
        for i, step in enumerate(plan): print "{}. {}".format(i+1, step)
        return plan

    def evaluate_step(self, last_action_description, goal, next_step_description):
        """Capture image and use Gemini Vision REST API for evaluation."""
        self.say("Let me check how that went.", blocking=True)

        # --- CORRECTED LINE ---
        # Capture image - Only expect 3 return values now
        eval_image_path, w, h = capture_image_nao(self.video, IMAGE_EVAL_FILENAME)
        # --- END CORRECTION ---

        if not eval_image_path: # Check if capture failed
            self.say("I couldn't take a picture to check.")
            return {"success": False, "reason": "Failed capture.", "advice": "Stop: Cannot evaluate."}

        # --- Read the *saved* PNG file for API ---
        # (Keep the file reading logic from the previous correction)
        img_bytes_for_api = None
        try:
            with open(eval_image_path, 'rb') as f: img_bytes_for_api = f.read()
            if not img_bytes_for_api: raise IOError("File is empty")
            print "Read {} bytes from saved evaluation PNG file.".format(len(img_bytes_for_api))
        except Exception, e: # Catch IOError and others
            print "Error reading saved eval image file {}: {}".format(eval_image_path, e)
            self.say("I had trouble reading the evaluation picture.")
            return {"success": False, "reason": "Failed reading eval img.", "advice": "Stop: Cannot evaluate."}

        # --- Build Evaluation Prompt - MORE STRICT ---
        # (Keep the strict prompt building logic from the previous correction)
        prompt = """
IMAGE ANALYSIS FOR ROBOT EVALUATION:
Analyze the attached image ({w}x{h}) considering the robot's recent actions.
Context: Goal="{goal}", Last Action="{last_action}", Next Action="{next_action}", Arm={arm}, Hand={hand}.
REQUIRED OUTPUT FORMAT: Generate ONLY a single, valid JSON object containing EXACTLY the following keys: "success": boolean, "current_state": string, "problems": string ("None" if ok), "advice": string (Must start with Proceed/Retry/Re-plan/Stop).
EXAMPLE: {{"success": false, "current_state": "Hand closed next to can", "problems": "Grasp missed", "advice": "Retry: Open, REACH_GRASP lower, CLOSE_HAND."}}
DO NOT include any other text, analysis, descriptions, lists, or formatting outside of this single JSON object.
""".format(w=w, h=h, goal=goal, last_action=last_action_description, next_action=next_step_description if next_step_description else 'None (Final)', arm=self.arm, hand=self.hand)
        # --- End Strict Prompt Building ---

        eval_response_text = call_gemini_vision_rest(prompt, img_bytes_for_api)
        eval_result = parse_json_from_gemini(eval_response_text) # Try parsing

        # ... (rest of the evaluation logic remains the same) ...
        if eval_result is None and vision_response_text:
             print "Warning: JSON parsing failed for evaluation. Raw response received."
             self.say("I couldn't understand the evaluation format.")
             return {"success": False, "reason": "Eval format error (raw received).", "advice": "Stop: Eval format error."}
        if eval_result is None:
            self.say("I'm having trouble evaluating the situation.")
            return {"success": False, "reason": "Eval parse/API fail.", "advice": "Stop: Evaluation failed."}
        if not isinstance(eval_result, dict) or not all(k in eval_result for k in ["success", "current_state", "problems", "advice"]):
             print "Error: Eval JSON format invalid or missing keys."; print eval_result
             return {"success": False, "reason": "Eval JSON format error.", "advice": "Stop: Eval format error."}
        print "\n--- Evaluation Result ---"
        print "Action Success (Visual): {}".format(eval_result.get('success'))
        print "Observed State: {}".format(eval_result.get('current_state'))
        print "Detected Problems: {}".format(eval_result.get('problems', 'N/A'))
        print "Recommendation: {}".format(eval_result.get('advice'))
        print "-------------------------\n"
        if eval_result.get('success'): self.say("Okay, that looks right.")
        else:
            problem_desc = eval_result.get('problems', 'something went wrong')
            if str(problem_desc).lower() == "none": problem_desc = "it didn't look quite right"
            self.say("Hmm, {}. My advice is: {}".format(problem_desc, eval_result.get('advice')))
        return eval_result

    def execute_step(self, step_command):
        """Parses and executes a single plan step using NAOqi."""
        # (This function remains largely the same as the previous Py2.7 version)
        print "Executing: {}".format(step_command)
        parts = step_command.split(' ', 1); command = parts[0].upper(); target_desc = parts[1] if len(parts) > 1 else None
        success = False
        try:
            if command == "OPEN_HAND": success = execute_open_hand(self.motion, self.hand)
            elif command == "CLOSE_HAND": success = execute_close_hand(self.motion, self.hand)
            elif command == "RELEASE": success = execute_open_hand(self.motion, self.hand) # Alias
            elif command == "LIFT": success = execute_move_arm_relative(self.motion, [0.0, 0.0, self.lift_height], self.arm, self.move_speed)
            elif command == "RETRACT_ARM": success = execute_move_arm_to_coords(self.motion, self.retract_position, self.arm, self.move_speed)
            elif command == "IDENTIFY":
                if target_desc and target_desc in self.world_model: print "Confirmed '{}'".format(target_desc); self.say("Okay, I see the {}.".format(target_desc)); success = True
                else: print "Error: Cannot IDENTIFY '{}'".format(target_desc); self.say("I don't have {} in sight.".format(target_desc)); success = False
            elif command == "REACH_PRE_GRASP" or command == "REACH_GRASP":
                if not target_desc: print "Error: REACH needs object label."; return False, command
                if target_desc not in self.world_model: print "Error: Cannot REACH '{}'".format(target_desc); self.say("I don't know where {} is.".format(target_desc)); return False, command
                base_target_coords = self.world_model[target_desc]; target_coords = list(base_target_coords)
                if command == "REACH_PRE_GRASP":
                    for i in xrange(3): target_coords[i] += self.pre_grasp_offset[i]
                    print "Calculated PRE_GRASP for {}: {}".format(target_desc, target_coords)
                else: print "Calculated GRASP for {}: {}".format(target_desc, target_coords)
                success = execute_move_arm_to_coords(self.motion, target_coords, self.arm, self.move_speed)
            elif command == "MOVE_ARM_TO_COORDS":
                if not target_desc: print "Error: MOVE_ARM_TO_COORDS needs '[x,y,z]'."; return False, command
                try:
                    target_coords = json.loads(target_desc.replace("'", '"')); assert isinstance(target_coords, list) and len(target_coords)==3
                    print "Moving arm to coords: {}".format(target_coords); success = execute_move_arm_to_coords(self.motion, target_coords, self.arm, self.move_speed)
                except (ValueError, TypeError, AssertionError), e: print "Error parsing MOVE_ARM_TO_COORDS: {}".format(e); self.say("Bad coordinates."); return False, command
            elif command == "MOVE_ARM_RELATIVE":
                 if not target_desc: print "Error: MOVE_ARM_RELATIVE needs '[dx,dy,dz]'."; return False, command
                 try:
                    offset = json.loads(target_desc.replace("'", '"')); assert isinstance(offset, list) and len(offset)==3
                    print "Moving arm relative by: {}".format(offset); success = execute_move_arm_relative(self.motion, offset, self.arm, self.move_speed)
                 except (ValueError, TypeError, AssertionError), e: print "Error parsing MOVE_ARM_RELATIVE: {}".format(e); self.say("Bad offset."); return False, command
            else: print "Error: Unknown command '{}'.".format(command); self.say("I cannot {}.".format(command)); return False, command

            if not success: print "Execution FAILED: {}".format(step_command); self.say("That didn't work.")
            else: print "Execution SUCCEEDED: {}".format(step_command); time.sleep(0.5)
            return success, command
        except Exception, e: print "Unexpected error executing step '{}': {}".format(step_command, e); traceback.print_exc(); self.say("Something went very wrong."); 
        try:
            self.motion.stopMove() 
        except: 
            pass; 
        return False, command

    def run_goal(self, goal):
        """Main execution loop for the task."""
        # (Orchestration logic remains the same as previous Py2.7 version)
        self.say("Received goal: {}".format(goal), blocking=True)
        max_retries = 1; current_retries = 0
        if not self.update_perception(): self.say("Perception failed. Cannot proceed."); return
        plan = self.plan_task(goal)
        if not plan: self.say("Planning failed. Cannot proceed."); return

        current_step_index = 0
        while 0 <= current_step_index < len(plan):
            step = plan[current_step_index]
            self.say("Step {}: {}".format(current_step_index + 1, step), blocking=False); time.sleep(0.7)
            success, command_executed = self.execute_step(step)
            if not success: self.say("Step failed. Stopping plan."); break
            time.sleep(1.0) # Pause before eval
            next_step_desc = plan[current_step_index + 1] if current_step_index + 1 < len(plan) else None
            last_action_desc = step # Use full step description for eval context
            eval_result = self.evaluate_step(last_action_desc, goal, next_step_desc)
            advice = eval_result.get("advice", "Stop: Unknown advice").lower()

            if advice.startswith("proceed"): print "Proceeding."; current_step_index += 1; current_retries = 0
            elif advice.startswith("retry") and current_retries < max_retries:
                current_retries += 1; self.say("Okay, retrying ({}/{}).".format(current_retries, max_retries)); print "Retrying step {}.".format(current_step_index+1); time.sleep(1.0)
            elif advice.startswith("re-plan"):
                self.say("Re-planning required."); print "Re-planning..."
                if not self.update_perception(): break
                new_plan = self.plan_task(goal)
                if not new_plan: break
                plan = new_plan; current_step_index = 0; current_retries = 0
            else: # Stop
                if advice.startswith("retry"): self.say("Max retries reached. Stopping.")
                else: self.say("Evaluation advises stopping.")
                print "Stopping execution based on advice: {}".format(eval_result.get('advice')); break

        if current_step_index == len(plan): print "\n--- Plan Completed ---"; self.say("Goal finished!")
        else: print "\n--- Plan Stopped ---"; self.say("Couldn't complete the goal.")

# ========================================
# ====        MAIN EXECUTION          ====
# ========================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NAO Robot Gemini Controller (Python 2.7 REST API - Restructured)")
    parser.add_argument("--ip", type=str, default=NAO_IP_DEFAULT, help="NAO robot IP address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=NAO_PORT, help="NAOqi port (default: %(default)s)")
    parser.add_argument("--speed", type=float, default=MOVE_SPEED_DEFAULT, help="Robot movement speed fraction (0.05-1.0, default: %(default)s)")
    parser.add_argument("--goal", type=str, required=True, help="The task goal for the robot (e.g., 'Pick up the red can')")
    args = parser.parse_args()

    # --- Validate Config ---
    if not (0.05 <= args.speed <= 1.0): print "Error: Speed must be between 0.05 and 1.0"; sys.exit(1)
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY": print "Error: Please set GEMINI_API_KEY in the script."; sys.exit(1)

    # --- Initialize Proxies ---
    print "==== CONNECTING TO NAO ===="
    all_proxies = None
    motion_proxy = None
    posture_proxy = None
    try:
        # Store all proxies in a dictionary
        all_proxies = {}
        proxy_names = ["ALMotion", "ALRobotPosture", "ALVideoDevice", "ALTextToSpeech", "ALMemory"]
        for name in proxy_names:
            print "Connecting to {}...".format(name)
            all_proxies[name] = ALProxy(name, args.ip, args.port)
        print "All proxies connected."

        # Assign frequently used proxies for convenience
        motion_proxy = all_proxies['ALMotion']
        posture_proxy = all_proxies['ALRobotPosture']
        tts_proxy = all_proxies['ALTextToSpeech'] # Use this for initial messages

    except Exception, e:
        print "FATAL: Failed to connect to NAO proxies: {}".format(e)
        sys.exit(1) # Exit if connection fails

    # --- Main Task Execution with Cleanup ---
    controller = None
    try:
        # -- Initialize Robot State --
        print "\n==== INITIALIZING ROBOT STATE ===="
        tts_proxy.say("Starting up.") # Use TTS proxy
        print "Waking up..."
        motion_proxy.wakeUp()
        print "Going to StandInit posture..."
        posture_proxy.goToPosture("StandInit", 0.8)
        time.sleep(1.0) # Wait for posture
        print "Setting initial stiffness..."
        motion_proxy.setStiffnesses("Body", STIFFNESS_ON)
        print "Moving head down to look..."
        motion_proxy.angleInterpolationWithSpeed("HeadPitch", HEAD_PITCH_LOOK_DOWN, HEAD_MOVEMENT_SPEED)
        time.sleep(2.0) # Wait for head movement

        # -- Create and Run Controller --
        controller = NAOGeminiController(all_proxies, args.speed)
        print "\n==== RUNNING GOAL: {} ====\n".format(args.goal)
        controller.run_goal(args.goal)
        print "\n==== GOAL EXECUTION FINISHED ===="

    except KeyboardInterrupt:
        print "\nCaught KeyboardInterrupt. Exiting task loop."
        if 'ALTextToSpeech' in all_proxies: all_proxies['ALTextToSpeech'].say("Stopping.")
    except Exception, e: # Catch errors during controller run
        print "\nFATAL ERROR during task execution: {}".format(e)
        traceback.print_exc()
        if 'ALTextToSpeech' in all_proxies: all_proxies['ALTextToSpeech'].say("An error occurred.")
    finally:
        # --- Cleanup ---
        print "\n==== CLEANUP AND SHUTDOWN ===="
        if motion_proxy: # Check if motion_proxy was successfully created
             try:
                 print "Stopping any ongoing motion..."
                 motion_proxy.stopMove()
                 print "Moving head back to neutral..."
                 # Use angleInterpolationWithSpeed for smoother head reset
                 motion_proxy.angleInterpolationWithSpeed("HeadPitch", 0.0, HEAD_MOVEMENT_SPEED)
                 time.sleep(1.5) # Wait for head movement
                 print "Resting robot..."
                 motion_proxy.rest() # Handles stiffness automatically
                 print "Robot rested."
             except Exception, cleanup_e:
                 print "Error during motion cleanup: {}".format(cleanup_e)
        else:
             print "Motion proxy not available for cleanup."

        if 'ALTextToSpeech' in all_proxies:
            try:
                all_proxies['ALTextToSpeech'].say("Goodbye.")
                time.sleep(1.0) # Allow speech to finish
            except: pass # Ignore errors during final TTS

        print "Program terminated."