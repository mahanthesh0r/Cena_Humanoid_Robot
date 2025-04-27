#!/usr/bin/env python
# -*- coding: utf-8 -*-

# === Python 2.7 NAO Gemini Planning & Evaluation Controller (Corrected & Indented) ===
# Uses REST API for Gemini perception, planning, and evaluation.

import sys
import time
import json
import urllib2
import base64
import io
import os
import math
import argparse
import traceback

# Attempt to import NAOqi SDK
try:
    from naoqi import ALProxy
    import vision_definitions
    import motion
except ImportError:
    print "--------------------------------------------------"
    print " Error: NAOqi SDK not found or not in PYTHONPATH."
    print "--------------------------------------------------"
    sys.exit(1)

# Attempt to import Pillow
try:
    from PIL import Image
except ImportError:
    print "--------------------------------------------------"
    print " Error: Pillow (PIL) not found."
    print "--------------------------------------------------"
    sys.exit(1)


# ========================================
# ====        CONFIGURATION           ====
# ========================================

# -- NAO Connection --
NAO_IP_DEFAULT = "169.254.79.239"
NAO_PORT = 9559

# -- Gemini API --
GEMINI_API_KEY = "AIzaSyBRPzDg8HrjvIXY9sryD40onS5JeUwvHSA" # !!! REPLACE THIS !!!
GEMINI_REST_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models/"
GEMINI_TEXT_MODEL = "gemini-1.5-flash-latest"
GEMINI_VISION_MODEL = "gemini-1.5-flash-latest"

# -- Camera Settings --
CAMERA_ID = 1
RESOLUTION = vision_definitions.kQVGA
COLOR_SPACE = vision_definitions.kRGBColorSpace
FPS = 5

# -- Motion Settings --
MOVE_SPEED_DEFAULT = 0.20
HEAD_MOVEMENT_SPEED = 0.20
HEAD_PITCH_LOOK_DOWN = 0.6
POSTURE_SPEED = 0.6
DEFAULT_ARM = "RArm"
DEFAULT_HAND = "RHand"
STIFFNESS_ON = 0.85

# --- Coordinate Estimation ---
TARGET_PLANE_Z_IN_ROBOT_FRAME = 0.0 # Ground level

# --- Arm Reach Parameters ---
PRE_GRASP_OFFSET = [-0.03, 0.0, 0.08]
LIFT_HEIGHT_OFFSET = [0.0, 0.0, 0.08]
RETRACT_POSITION_STAND = [0.15, 0.0, 0.25]

# -- Robot Frames / Constants --
FRAME = motion.FRAME_ROBOT
AXIS_MASK_ALL = 7

# --- File Paths ---
IMAGE_CAPTURE_FILENAME = "nao_capture.png"
IMAGE_EVAL_FILENAME = "nao_eval.png"

# ========================================
# ====    NAOQI HELPER FUNCTIONS      ====
# ========================================
def execute_open_hand(motion_proxy, hand_name):
    print "Opening {}...".format(hand_name)
    try:
        motion_proxy.openHand(hand_name)
        print "{} opened.".format(hand_name)
        return True
    except Exception, e:
        print "Error opening hand {}: {}".format(hand_name, e)
        return False

def execute_close_hand(motion_proxy, hand_name):
    print "Closing {}...".format(hand_name)
    try:
        motion_proxy.closeHand(hand_name)
        print "{} closed.".format(hand_name)
        return True
    except Exception, e:
        print "Error closing hand {}: {}".format(hand_name, e)
        return False

def execute_move_arm_to_coords(motion_proxy, target_coords, arm_name, speed):
    print "Moving {} to [{:.3f}, {:.3f}, {:.3f}] at speed {:.2f}...".format(arm_name, target_coords[0], target_coords[1], target_coords[2], speed)
    try:
        if not isinstance(target_coords, list) or len(target_coords) != 3:
            print "Error: Target coords list invalid."
            return False
        target_orientation = [0.0, math.radians(20.0), 0.0]
        full_target = target_coords + target_orientation
        motion_proxy.setPositions(arm_name, FRAME, full_target, speed, AXIS_MASK_ALL)
        motion_proxy.waitUntilMoveIsFinished()
        print "{} move complete.".format(arm_name)
        return True
    except Exception, e:
        print "Error moving arm {} to coords: {}".format(arm_name, e)
        return False

def execute_move_arm_relative(motion_proxy, offset, arm_name, speed):
     print "Moving {} relatively by [{:.3f}, {:.3f}, {:.3f}]...".format(arm_name, offset[0], offset[1], offset[2])
     try:
          current_pos = motion_proxy.getPosition(arm_name, FRAME, True)
          if len(current_pos) < 3:
              print "Error: Could not get current arm position."
              return False
          target_coords = [current_pos[i] + offset[i] for i in range(3)]
          return execute_move_arm_to_coords(motion_proxy, target_coords, arm_name, speed)
     except Exception, e:
          print "Error moving arm {} relatively: {}".format(arm_name, e)
          return False

def execute_go_to_posture(posture_proxy, posture_name="StandInit", speed=0.5):
    print "Going to posture: {} at speed {:.2f}...".format(posture_name, speed)
    try:
        success = posture_proxy.goToPosture(posture_name, speed)
        print "Posture {}.".format("reached" if success else "failed")
        return success
    except Exception, e:
        print "Error going to posture {}: {}".format(posture_name, e)
        return False

# ========================================
# ====    GEMINI HELPER FUNCTIONS     ====
# ========================================
def call_gemini_rest_api(model_name, payload, api_key):
    print "\n--- Calling Gemini REST API ({}) ---".format(model_name)
    url = "{}{}:generateContent?key={}".format(GEMINI_REST_API_BASE, model_name, api_key)
    json_payload = json.dumps(payload)
    try:
        request = urllib2.Request(url, data=json_payload, headers={'Content-Type': 'application/json'})
        response = urllib2.urlopen(request, timeout=90)
        response_body = response.read()
        response_code = response.getcode()
        print "Gemini API Response Code: {}".format(response_code)
        if response_code == 200:
            try:
                parsed_json = json.loads(response_body)
                if not parsed_json or not parsed_json.get('candidates'):
                    print "Error: Gemini response missing 'candidates'."
                    print "Full Response: {}".format(parsed_json)
                    # ... (rest of error checking) ...
                    return None
                return parsed_json
            except ValueError, e:
                print "Error parsing JSON from Gemini: {}".format(e)
                print "Received: {}".format(response_body)
                return None
        else:
            print "Error: Gemini API failed status {}. Resp: {}".format(response_code, response_body)
            return None
    except urllib2.HTTPError, e:
        print "HTTP Error calling Gemini: {} {}".format(e.code, e.reason)
        try:
            print "Error Body: {}".format(e.read())
        except:
            pass
        return None
    except urllib2.URLError, e:
        print "URL Error calling Gemini: {}".format(e.reason)
        return None
    except Exception, e:
        print "Unexpected Error calling Gemini API: {}".format(e)
        traceback.print_exc()
        return None

def call_gemini_text_rest(prompt):
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    response_json = call_gemini_rest_api(GEMINI_TEXT_MODEL, payload, GEMINI_API_KEY)
    if response_json and response_json.get('candidates'):
        try:
            return response_json['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError, TypeError), e:
            print "Error parsing text from Gemini response: {}".format(e)
            print response_json
            return None
    return None

def call_gemini_vision_rest(prompt, image_bytes, mime_type="image/png"):
    try:
        base64_image = base64.b64encode(image_bytes)
    except Exception, e:
        print "Error Base64 encoding image: {}".format(e)
        return None
    payload = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": mime_type, "data": base64_image}}]}]}
    response_json = call_gemini_rest_api(GEMINI_VISION_MODEL, payload, GEMINI_API_KEY)
    if response_json and response_json.get('candidates'):
        try:
            return response_json['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError, TypeError), e:
            print "Error parsing text from Gemini vision response: {}".format(e)
            print response_json
            return None
    return None

def parse_json_from_gemini(response_text):
    if not response_text:
        return None
    try:
        cleaned_text = response_text.strip()
        if cleaned_text.startswith("```json"):
            cleaned_text = cleaned_text[7:]
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3]
        cleaned_text = cleaned_text.strip()
        if not cleaned_text:
            print "Warning: Cleaned Gemini response is empty."
            return None
        return json.loads(cleaned_text)
    except ValueError, e:
        print "Error parsing JSON: {}".format(e)
        print "Received text:\n---\n{}\n---".format(response_text)
        return None
    except Exception, e:
        print "Unexpected error during JSON parsing: {}".format(e)
        return None

# ========================================
# ==== IMAGE & COORD HELPER FUNCTIONS ====
# ========================================
def capture_image_nao(video_proxy, filename):
    print "Capturing image from camera ID {}...".format(CAMERA_ID)
    subscriber_id = "pyclient_{}".format(int(time.time()))
    video_client = None
    try:
        video_client = video_proxy.subscribeCamera(subscriber_id, CAMERA_ID, RESOLUTION, COLOR_SPACE, FPS)
        if not video_client:
            print "Error: Cannot subscribe."
            return None, None, None
        time.sleep(0.3)
        nao_image = video_proxy.getImageRemote(video_client)
        video_proxy.unsubscribe(video_client)
        video_client = None
        if nao_image is None or len(nao_image) < 7:
            print "Error: Cannot retrieve image."
            return None, None, None
        w, h, layers, img_data = nao_image[0], nao_image[1], nao_image[2], str(nao_image[6])
        if layers != 3:
            print "Warning: Expected 3 color layers, got {}".format(layers)
        img = Image.frombytes("RGB", (w, h), img_data)
        img.save(filename)
        print "Image saved as {} ({}x{})".format(filename, w, h)
        return filename, w, h
    except Exception, e:
        print "Error capturing image: {}".format(e)
        if video_client:
            try:
                video_proxy.unsubscribe(video_client)
            except:
                pass
        return None, None, None

def pixels_to_world_estimation(box_center_px, image_dims_px, motion_proxy):
    width_px, height_px = image_dims_px
    camera_name = "CameraTop" if CAMERA_ID == 0 else "CameraBottom"
    if not width_px or not height_px:
        print "Error: Invalid image dimensions for coord estimation."
        return None
    try:
        x_px, y_px = box_center_px
        print "Estimating world coords for pixel: {} using trigonometric projection.".format(box_center_px)
        head_angles = motion_proxy.getAngles(["HeadYaw", "HeadPitch"], True)
        head_yaw, head_pitch = head_angles
        cam_transform_matrix = motion_proxy.getTransform(camera_name, FRAME, True)
        cam_pos_x, cam_pos_y, cam_pos_z = cam_transform_matrix[3], cam_transform_matrix[7], cam_transform_matrix[11]
        print "  -> Camera '{}' Pos: [{:.3f}, {:.3f}, {:.3f}]".format(camera_name, cam_pos_x, cam_pos_y, cam_pos_z)
        print "  -> Head Angles (Sensor): Yaw={:.1f} Pitch={:.1f} deg".format(math.degrees(head_yaw), math.degrees(head_pitch))
        norm_x = (float(x_px) / width_px) - 0.5
        norm_y = (float(y_px) / height_px) - 0.5
        if CAMERA_ID == 0:
            fov_h_deg, fov_v_deg = 60.9, 47.6
        else:
            fov_h_deg, fov_v_deg = 63.9, 49.9 # Bottom Camera FOV
        print "  -> Using {} Camera FOV ({:.1f} H, {:.1f} V)".format(camera_name, fov_h_deg, fov_v_deg)
        angle_offset_yaw = -norm_x * math.radians(fov_h_deg)
        angle_offset_pitch = norm_y * math.radians(fov_v_deg)
        print "  -> Pixel Offsets (Angle): dYaw={:.1f} dPitch={:.1f} deg".format(math.degrees(angle_offset_yaw), math.degrees(angle_offset_pitch))
        final_yaw = head_yaw + angle_offset_yaw
        combined_pitch = head_pitch + angle_offset_pitch
        print "  -> Approx Final Pointing: Yaw={:.1f} Pitch={:.1f} deg".format(math.degrees(final_yaw), math.degrees(combined_pitch))
        target_z = TARGET_PLANE_Z_IN_ROBOT_FRAME
        delta_z = cam_pos_z - target_z
        print "  -> Target Plane Z: {:.3f}".format(target_z)
        print "  -> Camera Z: {:.3f}".format(cam_pos_z)
        print "  -> Delta Z: {:.3f}".format(delta_z)
        if delta_z <= 0.01:
            print "Warning: Cam Z <= Target Z."
            return None
        depression_angle_rad = -combined_pitch
        if depression_angle_rad <= math.radians(1.0):
            print "Warning: Depression angle ({:.1f} deg) too small or negative.".format(math.degrees(depression_angle_rad))
            return None
        try:
            dist_on_xy_plane = delta_z / math.tan(depression_angle_rad)
        except (ValueError, ZeroDivisionError), e:
            print "Warning: Math error during tan calculation: {}".format(e)
            return None
        if dist_on_xy_plane < 0:
            print "Warning: Calculated negative distance ({:.3f}m). Check logic.".format(dist_on_xy_plane)
            return None
        print "  -> Depression Angle: {:.1f} deg".format(math.degrees(depression_angle_rad))
        print "  -> Calculated Horizontal Distance: {:.3f}m".format(dist_on_xy_plane)
        obj_x = cam_pos_x + dist_on_xy_plane * math.cos(final_yaw)
        obj_y = cam_pos_y + dist_on_xy_plane * math.sin(final_yaw)
        obj_z = target_z
        estimated_coords = [obj_x, obj_y, obj_z]
        print "  -> Final Estimated Coords (FRAME_ROBOT): [{:.3f}, {:.3f}, {:.3f}]".format(estimated_coords[0], estimated_coords[1], estimated_coords[2])
        if not (0.10 < obj_x < 0.60 and abs(obj_y) < 0.40 and -0.1 < obj_z < 0.5):
            print "Warning: Final estimated coordinates outside interaction range."
        return estimated_coords
    except Exception, e:
        print "Error during full pixel_to_world estimation: {}".format(e)
        traceback.print_exc()
        return None

# ========================================
# ====    MAIN CONTROLLER CLASS       ====
# ========================================

class NAOGeminiController(object): # Added (object) for new-style class
    """ Controller using Gemini for planning and evaluation. """
    def __init__(self, nao_proxies, move_speed_param):
        if not nao_proxies:
            raise RuntimeError("Failed to initialize: Missing NAO proxies.")
        self.proxies = nao_proxies
        self.motion = self.proxies['ALMotion']
        self.video = self.proxies['ALVideoDevice']
        self.tts = self.proxies['ALTextToSpeech']
        self.posture = self.proxies['ALRobotPosture']

        self.world_model = {}
        self.current_image_path = None
        self.image_dims = (None, None)
        self.move_speed = move_speed_param
        self.arm = DEFAULT_ARM
        self.hand = DEFAULT_HAND
        self.pre_grasp_offset = PRE_GRASP_OFFSET
        self.lift_height_offset = LIFT_HEIGHT_OFFSET
        self.retract_position = RETRACT_POSITION_STAND

    def say(self, text, blocking=False):
        try:
            safe_text = str(text)
            if blocking:
                self.tts.say(safe_text)
            else:
                self.tts.post.say(safe_text)
        except Exception, e:
            print "TTS Error: {}".format(e)

    def update_perception(self):
        self.say("Let me take a look.", blocking=True)
        self.current_image_path, w, h = capture_image_nao(self.video, IMAGE_CAPTURE_FILENAME)
        if not self.current_image_path:
            self.say("I couldn't see clearly.")
            return False
        self.image_dims = (w, h)
        img_bytes_for_api = None
        try:
            with open(self.current_image_path, 'rb') as f:
                img_bytes_for_api = f.read()
            if not img_bytes_for_api:
                raise IOError("File empty")
            print "Read {} bytes from saved PNG file.".format(len(img_bytes_for_api))
        except Exception, e:
            print "Error reading saved image {}: {}".format(self.current_image_path, e)
            self.say("Trouble reading picture.")
            return False

        prompt_template = """Analyze attached image ({}x{}). Detect distinct, graspable objects on surface. Focus: cans, bottles, blocks, cups. For each: Provide label (e.g., 'red can') and bounding box [xmin, ymin, xmax, ymax] within [0, {}] and [0, {}]. Output ONLY valid JSON list. Example: [{{"label": "red can", "box": [100, 150, 180, 300]}}]"""
        example_json_string = ""
        closing_instructions = """If none, return []. Strict JSON only."""
        formatted_part = prompt_template.format(w, h, w-1, h-1)
        prompt = "\n".join([formatted_part, example_json_string, closing_instructions])

        vision_response_text = call_gemini_vision_rest(prompt, img_bytes_for_api)
        detected_objects = parse_json_from_gemini(vision_response_text)

        if detected_objects is None:
            self.say("Trouble understanding image.")
            return False
        self.world_model = {}
        valid_detections = 0
        if not isinstance(detected_objects, list):
            print "Error: Detections not a list"
            return False
        if not detected_objects:
            self.say("I don't see any objects.")
            return True

        print "Processing detected objects:"
        for obj in detected_objects:
            if isinstance(obj, dict) and 'label' in obj and 'box' in obj:
                label = obj['label']
                box = obj['box']
                if not (isinstance(box, list) and len(box) == 4 and all(isinstance(n, int) for n in box)):
                    print "  - Skipping '{}': Invalid box format {}".format(label, box)
                    continue
                if not (0 <= box[0] < box[2] < w and 0 <= box[1] < box[3] < h):
                    print "  - Skipping '{}': Box out of bounds {} in ({}x{})".format(label, box, w, h)
                    continue
                center_x = (box[0] + box[2]) / 2.0
                center_y = (box[1] + box[3]) / 2.0
                coords = pixels_to_world_estimation((center_x, center_y), self.image_dims, self.motion)
                if coords:
                    self.world_model[label] = coords
                    print "  + Found '{}' at estimated coords [{:.3f}, {:.3f}, {:.3f}]".format(label, coords[0], coords[1], coords[2])
                    valid_detections += 1
                else:
                    print "  - Skipping '{}': Failed coord estimation.".format(label)
            else:
                print "Warning: Skipping malformed object entry: {}".format(obj)
        if valid_detections > 0:
            self.say("Okay, I see {} object{}.".format(valid_detections, 's' if valid_detections > 1 else ''))
        else:
            self.say("Couldn't pinpoint objects.")
        print "\n--- World Model Updated ---"
        if self.world_model:
            for label, coords in self.world_model.iteritems():
                print "- {}: [{:.3f}, {:.3f}, {:.3f}]".format(label, coords[0], coords[1], coords[2])
        else:
            print "No objects with valid coordinates in model."
        print "---------------------------\n"
        return True

    def plan_task(self, goal):
        if not self.world_model:
            self.say("No objects seen, cannot plan.")
            return None
        self.say("Okay, planning how to: {}".format(goal), blocking=True)
        prompt = """You are planning assistant for NAO robot. Goal: "{}". World State: {}. Robot Resources: Arm={}, Hand={}(starts open). Generate sequence of steps. Use ONLY verbs: IDENTIFY <label>, OPEN_HAND, REACH_PRE_GRASP <label>, REACH_GRASP <label>, CLOSE_HAND, LIFT, MOVE_ARM_TO_COORDS <[x,y,z]>, MOVE_ARM_RELATIVE <[dx,dy,dz]>, RELEASE, RETRACT_ARM. Logic: Open before grasp, pre-grasp before grasp, lift after grasp, release after move, retract after release. Handle relative placements if possible. Output ONLY numbered list.""".format(goal, json.dumps(self.world_model, indent=2) if self.world_model else "No objects.", self.arm, self.hand)

        plan_response_text = call_gemini_text_rest(prompt)
        if not plan_response_text:
            self.say("Couldn't make a plan.")
            return None
        try:
            plan = []
            raw_lines = plan_response_text.strip().split('\n')
            for line in raw_lines:
                line_strip = line.strip()
                if line_strip and line_strip[0].isdigit():
                    first_space_index = -1
                    for i, char in enumerate(line_strip):
                        if char == ' ' and i > 0:
                            is_prefix_num = all(line_strip[j].isdigit() or line_strip[j] == '.' for j in xrange(i))
                            if is_prefix_num:
                                first_space_index = i
                                break
                    if first_space_index != -1:
                        action = line_strip[first_space_index+1:].strip()
                        if action:
                            plan.append(action)
            if not plan and raw_lines:
                 print "Warning: Using raw lines for plan."
                 plan = [line.strip() for line in raw_lines if line.strip()]
        except Exception, e:
            print "Error parsing plan: {}".format(e)
            print "Raw:\n{}".format(plan_response_text)
            self.say("Planning failed.")
            return None
        if not plan:
            print "Warning: Could not extract plan."
            self.say("Plan unclear.")
            return None
        self.say("Plan has {} steps.".format(len(plan)))
        print "\n--- Generated Plan ---"
        for i, step in enumerate(plan):
            print "{}. {}".format(i+1, step)
        print "----------------------\n"
        return plan

    def evaluate_step(self, last_action_description, goal, next_step_description):
        self.say("Let me check how that went.", blocking=True)
        eval_image_path, w, h = capture_image_nao(self.video, IMAGE_EVAL_FILENAME)
        if not eval_image_path:
            self.say("Couldn't take picture.")
            return {"success": False, "reason": "Failed capture.", "advice": "Stop: Cannot evaluate."}
        img_bytes_for_api = None
        try:
            with open(eval_image_path, 'rb') as f:
                img_bytes_for_api = f.read()
            if not img_bytes_for_api:
                raise IOError("File empty")
            print "Read {} bytes from saved eval PNG.".format(len(img_bytes_for_api))
        except Exception, e:
            print "Error reading eval image {}: {}".format(eval_image_path, e)
            self.say("Trouble reading eval picture.")
            return {"success": False, "reason": "Failed reading eval.", "advice": "Stop: Cannot evaluate."}

        prompt = """IMAGE ANALYSIS FOR ROBOT EVALUATION: Analyze attached image ({w}x{h}). Context: Goal="{goal}", Last Action="{last_action}", Next Action="{next_action}", Arm={arm}, Hand={hand}. REQUIRED OUTPUT FORMAT: Generate ONLY single valid JSON: {{"success": bool, "current_state": str, "problems": str ("None" if ok), "advice": str (Must start Proceed:/Retry:/Re-plan:/Stop: + reason)}}. EXAMPLE: {{"success": false, "current_state": "Hand closed next to can", "problems": "Grasp missed", "advice": "Retry: Open, REACH_GRASP lower, CLOSE_HAND."}} DO NOT include any other text.""".format(w=w, h=h, goal=goal, last_action=last_action_description, next_action=next_step_description if next_step_description else 'None (Final)', arm=self.arm, hand=self.hand)

        eval_response_text = call_gemini_vision_rest(prompt, img_bytes_for_api)
        eval_result = parse_json_from_gemini(eval_response_text)

        if eval_result is None and vision_response_text:
             print "Warning: JSON parse failed for eval."
             self.say("Couldn't understand eval format.")
             return {"success": False, "reason": "Eval format error.", "advice": "Stop: Eval format error."}
        if eval_result is None:
            self.say("Trouble evaluating.")
            return {"success": False, "reason": "Eval parse/API fail.", "advice": "Stop: Evaluation failed."}
        if not isinstance(eval_result, dict) or not all(k in eval_result for k in ["success", "current_state", "problems", "advice"]):
             print "Error: Eval JSON format invalid."
             print eval_result
             return {"success": False, "reason": "Eval JSON format error.", "advice": "Stop: Eval format error."}

        print "\n--- Evaluation Result ---"
        print "Action Success (Visual): {}".format(eval_result.get('success'))
        print "Observed State: {}".format(eval_result.get('current_state'))
        print "Detected Problems: {}".format(eval_result.get('problems', 'N/A'))
        print "Recommendation: {}".format(eval_result.get('advice'))
        print "-------------------------\n"
        if eval_result.get('success'):
            self.say("Okay, that looks right.")
        else:
            problem_desc = eval_result.get('problems', 'something went wrong')
            if str(problem_desc).lower() == "none":
                problem_desc = "it didn't look quite right"
            self.say("Hmm, {}. My advice is: {}".format(problem_desc, eval_result.get('advice')))
        return eval_result

    def execute_step(self, step_command):
        print "Executing: {}".format(step_command)
        parts = step_command.split(' ', 1)
        command = parts[0].upper()
        target_desc = parts[1] if len(parts) > 1 else None
        success = False
        command_executed = command

        try:
            if command == "OPEN_HAND":
                success = execute_open_hand(self.motion, self.hand)
            elif command == "CLOSE_HAND":
                success = execute_close_hand(self.motion, self.hand)
            elif command == "RELEASE":
                success = execute_open_hand(self.motion, self.hand)
            elif command == "LIFT":
                success = execute_move_arm_relative(self.motion, self.lift_height_offset, self.arm, self.move_speed * 0.7)
            elif command == "RETRACT_ARM":
                success = execute_move_arm_to_coords(self.motion, self.retract_position, self.arm, self.move_speed)
            elif command == "IDENTIFY":
                if target_desc and target_desc in self.world_model:
                    print "Confirmed '{}' in model.".format(target_desc)
                    self.say("Okay, I see {}.".format(target_desc))
                    success = True
                else:
                    print "Error: Cannot IDENTIFY '{}'".format(target_desc)
                    self.say("I don't see {}.".format(target_desc))
                    success = False
            elif command == "REACH_PRE_GRASP" or command == "REACH_GRASP":
                if not target_desc:
                    print "Error: REACH needs object label."
                    return False, command
                if target_desc not in self.world_model:
                    print "Error: Cannot REACH '{}'".format(target_desc)
                    self.say("I don't know where {} is.".format(target_desc))
                    return False, command
                base_target_coords = self.world_model[target_desc]
                target_coords = list(base_target_coords) # Copy
                if command == "REACH_PRE_GRASP":
                    for i in xrange(3): # Use xrange in Py2
                        target_coords[i] += self.pre_grasp_offset[i] # Apply offset
                    print "Calculated PRE_GRASP for {}: {}".format(target_desc, ["{:.3f}".format(c) for c in target_coords])
                else: # REACH_GRASP
                    print "Calculated GRASP for {}: {}".format(target_desc, ["{:.3f}".format(c) for c in target_coords])
                success = execute_move_arm_to_coords(self.motion, target_coords, self.arm, self.move_speed)
            elif command == "MOVE_ARM_TO_COORDS":
                if not target_desc:
                    print "Error: MOVE_ARM_TO_COORDS needs '[x,y,z]'."
                    return False, command
                try:
                    target_coords = json.loads(target_desc.replace("'", '"'))
                    assert isinstance(target_coords, list) and len(target_coords)==3
                    print "Moving arm to coords: {}".format(target_coords)
                    success = execute_move_arm_to_coords(self.motion, target_coords, self.arm, self.move_speed)
                except (ValueError, TypeError, AssertionError), e:
                    print "Error parsing MOVE_ARM_TO_COORDS: {}".format(e)
                    self.say("Bad coordinates.")
                    return False, command
            elif command == "MOVE_ARM_RELATIVE":
                 if not target_desc:
                     print "Error: MOVE_ARM_RELATIVE needs '[dx,dy,dz]'."
                     return False, command
                 try:
                    offset = json.loads(target_desc.replace("'", '"'))
                    assert isinstance(offset, list) and len(offset)==3
                    print "Moving arm relative by: {}".format(offset)
                    success = execute_move_arm_relative(self.motion, offset, self.arm, self.move_speed)
                 except (ValueError, TypeError, AssertionError), e:
                    print "Error parsing MOVE_ARM_RELATIVE: {}".format(e)
                    self.say("Bad offset.")
                    return False, command
            else:
                print "Error: Unknown command '{}'.".format(command)
                self.say("I cannot {}.".format(command))
                return False, command

            if not success:
                print "Execution FAILED: {}".format(step_command)
                self.say("That didn't work.")
            else:
                print "Execution SUCCEEDED: {}".format(step_command)
                time.sleep(0.5)
            return success, command_executed # Return command executed
        except Exception, e:
            print "Unexpected error executing step '{}': {}".format(step_command, e)
            traceback.print_exc()
            self.say("Something went very wrong.")
            try:
                self.motion.stopMove()
            except:
                pass
            return False, command # Return command even on failure

    def run_goal(self, goal):
        self.say("Received goal: {}".format(goal), blocking=True)
        max_retries = 1
        current_retries = 0
        if not self.update_perception():
            self.say("Perception failed. Cannot proceed.")
            return
        plan = self.plan_task(goal)
        if not plan:
            self.say("Planning failed. Cannot proceed.")
            return

        current_step_index = 0
        while 0 <= current_step_index < len(plan):
            step = plan[current_step_index]
            self.say("Step {}: {}".format(current_step_index + 1, step), blocking=False)
            time.sleep(0.7)
            success, command_executed = self.execute_step(step)
            if not success:
                self.say("Step failed. Stopping plan.")
                break
            time.sleep(1.0)
            next_step_desc = plan[current_step_index + 1] if current_step_index + 1 < len(plan) else None
            last_action_desc = step # Use full step description

            eval_result = self.evaluate_step(last_action_desc, goal, next_step_description)
            advice = eval_result.get("advice", "Stop: Unknown advice").lower()
            if advice.startswith("proceed"):
                print "Proceeding."
                current_step_index += 1
                current_retries = 0
            elif advice.startswith("retry") and current_retries < max_retries:
                current_retries += 1
                self.say("Okay, retrying ({}/{}).".format(current_retries, max_retries))
                print "Retrying step {}.".format(current_step_index+1)
                time.sleep(1.0)
            elif advice.startswith("re-plan"):
                self.say("Re-planning required.")
                print "Re-planning..."
                if not self.update_perception(): # Stop if perception fails
                    break
                new_plan = self.plan_task(goal)
                if not new_plan: # Stop if planning fails
                    break
                plan = new_plan
                current_step_index = 0
                current_retries = 0
            else: # Stop
                if advice.startswith("retry"):
                    self.say("Max retries reached. Stopping.")
                else:
                    self.say("Evaluation advises stopping.")
                print "Stopping execution based on advice: {}".format(eval_result.get('advice'))
                break
        if current_step_index == len(plan):
            print "\n--- Plan Completed ---"
            self.say("Goal finished!")
        else:
            print "\n--- Plan Stopped ---"
            self.say("Couldn't complete the goal.")


# ========================================
# ====        MAIN EXECUTION          ====
# ========================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NAO Robot Gemini Planning/Evaluation Controller (Python 2.7)")
    parser.add_argument("--ip", type=str, default=NAO_IP_DEFAULT, help="NAO robot IP address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=NAO_PORT, help="NAOqi port (default: %(default)s)")
    parser.add_argument("--speed", type=float, default=MOVE_SPEED_DEFAULT, help="Robot arm movement speed (default: %(default)s)")
    parser.add_argument("--goal", type=str, required=True, help="The task goal for the robot (e.g., 'pick up the fruit gummies')")
    args = parser.parse_args()

    if not (0.05 <= args.speed <= 1.0):
        print "Error: Speed must be between 0.05 and 1.0"
        sys.exit(1)
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY":
        print "Error: Please set GEMINI_API_KEY."
        sys.exit(1)

    print "==== CONNECTING TO NAO ===="
    all_proxies = None
    motion_proxy = None
    posture_proxy = None
    tts_proxy = None
    try:
        all_proxies = {}
        proxy_names = ["ALMotion", "ALRobotPosture", "ALVideoDevice", "ALTextToSpeech", "ALMemory"] # Added Memory back
        for name in proxy_names:
            print "Connecting to {}...".format(name)
            all_proxies[name] = ALProxy(name, args.ip, args.port)
        print "All proxies connected."
        motion_proxy = all_proxies['ALMotion']
        posture_proxy = all_proxies['ALRobotPosture']
        tts_proxy = all_proxies['ALTextToSpeech']
    except Exception, e:
        print "FATAL: Failed to connect to NAO proxies: {}".format(e)
        sys.exit(1)

    controller = None
    try:
        print "\n==== INITIALIZING ROBOT STATE ===="
        tts_proxy.say("Starting up.")
        print "Waking up..."
        motion_proxy.wakeUp()
        print "Going to StandInit posture..."
        posture_proxy.goToPosture("StandInit", 0.8)
        time.sleep(1.0)
        print "Setting initial stiffness..."
        motion_proxy.setStiffnesses("Body", STIFFNESS_ON)
        print "Moving head down to look..."
        motion_proxy.angleInterpolationWithSpeed("HeadPitch", HEAD_PITCH_LOOK_DOWN, HEAD_MOVEMENT_SPEED)
        time.sleep(1.0)
        try:
            current_head_angles = motion_proxy.getAngles(["HeadYaw", "HeadPitch"], True)
            print "DEBUG: Head angles after move cmd: Yaw={:.1f} Pitch={:.1f} deg".format(math.degrees(current_head_angles[0]), math.degrees(current_head_angles[1]))
        except Exception, e:
            print "DEBUG: Could not read head angles after move: {}".format(e)

        controller = NAOGeminiController(all_proxies, args.speed)
        print "\n==== RUNNING GOAL: {} ====\n".format(args.goal)
        controller.run_goal(args.goal)

        print "\n==== GOAL EXECUTION FINISHED ===="

    except KeyboardInterrupt:
        print "\nCaught KeyboardInterrupt. Exiting task."
        if tts_proxy:
            tts_proxy.say("Stopping.")
    except RuntimeError, e: # Catch connection/init errors
         print "Critical Error during initialization: {}".format(e)
    except Exception, e:
        print "\nFATAL ERROR during task execution: {}".format(e)
        traceback.print_exc()
        if tts_proxy:
            tts_proxy.say("An error occurred.")
    finally:
        print "\n==== CLEANUP AND SHUTDOWN ===="
        if motion_proxy:
             try:
                 print "Stopping any ongoing motion..."
                 motion_proxy.stopMove()
                 print "Moving head back to neutral..."
                 motion_proxy.angleInterpolationWithSpeed("HeadPitch", 0.0, HEAD_MOVEMENT_SPEED)
                 time.sleep(1.5)
                 print "Resting robot..."
                 motion_proxy.rest()
                 print "Robot rested."
             except Exception, cleanup_e:
                 print "Error during motion cleanup: {}".format(cleanup_e)
        else:
             print "Motion proxy not available for cleanup."
        if tts_proxy:
            try:
                tts_proxy.say("Goodbye.")
                time.sleep(1.0)
            except:
                pass
        print "Program terminated"