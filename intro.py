from naoqi import ALProxy
import time

# Replace with your robot's IP address
IP = "192.168.1.10"
PORT = 9559

# Create proxies
motion = ALProxy("ALMotion", IP, PORT)
posture = ALProxy("ALRobotPosture", IP, PORT)

# Step 1: Sit Down (Rest)
print("Robot is sitting down...")
motion.rest()
time.sleep(2)

# Step 2: Wake up and stand
print("Robot is waking up...")
motion.wakeUp()
posture.goToPosture("StandInit", 0.5)
time.sleep(1)

# Step 3: Walk Forward
print("Robot is walking forward...")
x = 0.5    # 0.5 meters forward
y = 0.0    # No left/right
theta = 0.0  # No rotation
motion.moveTo(x, y, theta)

# Wait for motion to complete
time.sleep(5)

# Optional: Sit down again
print("Robot is sitting down again...")
motion.rest()
