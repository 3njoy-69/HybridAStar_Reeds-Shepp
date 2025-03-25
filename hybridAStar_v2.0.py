import math
import sys
import os
import heapq
import time

import numpy as np
import matplotlib.pyplot as plt
from heapdict import heapdict
from scipy.spatial import KDTree
import reeds_shepp as rsCurve
import serial
import socket

# Cấu hình UDP server
UDP_IP = "0.0.0.0"  # Lắng nghe trên tất cả các địa chỉ
UDP_PORT = 5005      # Cổng UDP nhận dữ liệu
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

# Cấu hình UART
ser = serial.Serial(port = 'COM3', baudrate=115200, timeout=1)
time.sleep(1)

class Car:
    maxSteerAngle = 0.45
    steerPresion = 10
    wheelBase = 20
    axleToFront = 30
    axleToBack = 15
    width = 20

class Cost:
    reverse = 10
    directionChange = 150
    steerAngle = 1
    steerAngleChange = 5
    hybridCost = 50

class Node:
    def __init__(self, gridIndex, traj, steeringAngle, direction, cost, parentIndex):
        self.gridIndex = gridIndex         # grid block x, y, yaw index
        self.traj = traj                   # trajectory x, y  of a simulated node
        self.steeringAngle = steeringAngle # steering angle throughout the trajectory
        self.direction = direction         # direction throughout the trajectory
        self.cost = cost                   # node cost
        self.parentIndex = parentIndex     # parent node index

class HolonomicNode:
    def __init__(self, gridIndex, cost, parentIndex):
        self.gridIndex = gridIndex
        self.cost = cost
        self.parentIndex = parentIndex

class MapParameters:
    def __init__(self, mapMinX, mapMinY, mapMaxX, mapMaxY, xyResolution, yawResolution, ObstacleKDTree, obstacleX, obstacleY):
        self.mapMinX = mapMinX               # map min x coordinate(0)
        self.mapMinY = mapMinY               # map min y coordinate(0)
        self.mapMaxX = mapMaxX               # map max x coordinate
        self.mapMaxY = mapMaxY               # map max y coordinate
        self.xyResolution = xyResolution     # grid block length
        self.yawResolution = yawResolution   # grid block possible yaws
        self.ObstacleKDTree = ObstacleKDTree # KDTree representating obstacles
        self.obstacleX = obstacleX           # Obstacle x coordinate list
        self.obstacleY = obstacleY           # Obstacle y coordinate list

def calculateMapParameters(obstacleX, obstacleY, xyResolution, yawResolution):
        
        # calculate min max map grid index based on obstacles in map
        mapMinX = round(min(obstacleX) / xyResolution)
        mapMinY = round(min(obstacleY) / xyResolution)
        mapMaxX = round(max(obstacleX) / xyResolution)
        mapMaxY = round(max(obstacleY) / xyResolution)

        # create a KDTree to represent obstacles
        ObstacleKDTree = KDTree([[x, y] for x, y in zip(obstacleX, obstacleY)])

        return MapParameters(mapMinX, mapMinY, mapMaxX, mapMaxY, xyResolution, yawResolution, ObstacleKDTree, obstacleX, obstacleY)  

def index(Node):
    # Index is a tuple consisting grid index, used for checking if two nodes are near/same
    return tuple([Node.gridIndex[0], Node.gridIndex[1], Node.gridIndex[2]])

def motionCommands():

    # Motion commands for a Non-Holonomic Robot like a Car or Bicycle (Trajectories using Steer Angle and Direction)
    direction = 1
    motionCommand = []
    for i in np.arange(Car.maxSteerAngle, -(Car.maxSteerAngle + Car.maxSteerAngle/Car.steerPresion), -Car.maxSteerAngle/Car.steerPresion):
        motionCommand.append([i, direction])
        motionCommand.append([i, -direction])
    return motionCommand

def holonomicMotionCommands():

    # Action set for a Point/Omni-Directional/Holonomic Robot (8-Directions)
    holonomicMotionCommand = [[-1, 0], [-1, 1], [0, 1], [1, 1], [1, 0], [1, -1], [0, -1], [-1, -1]]
    return holonomicMotionCommand


def kinematicSimulationNode(currentNode, motionCommand, mapParameters, simulationLength=4, step = 0.8 ):

    # Simulate node using given current Node and Motion Commands
    traj = []
    angle = rsCurve.pi_2_pi(currentNode.traj[-1][2] + motionCommand[1] * step / Car.wheelBase * math.tan(motionCommand[0]))
    traj.append([currentNode.traj[-1][0] + motionCommand[1] * step * math.cos(angle),
                currentNode.traj[-1][1] + motionCommand[1] * step * math.sin(angle),
                rsCurve.pi_2_pi(angle + motionCommand[1] * step / Car.wheelBase * math.tan(motionCommand[0]))])
    for i in range(int((simulationLength/step))-1):
        traj.append([traj[i][0] + motionCommand[1] * step * math.cos(traj[i][2]),
                    traj[i][1] + motionCommand[1] * step * math.sin(traj[i][2]),
                    rsCurve.pi_2_pi(traj[i][2] + motionCommand[1] * step / Car.wheelBase * math.tan(motionCommand[0]))])

    # Find grid index
    gridIndex = [round(traj[-1][0]/mapParameters.xyResolution), \
                 round(traj[-1][1]/mapParameters.xyResolution), \
                 round(traj[-1][2]/mapParameters.yawResolution)]

    # Check if node is valid
    if not isValid(traj, gridIndex, mapParameters):
        return None

    # Calculate Cost of the node
    cost = simulatedPathCost(currentNode, motionCommand, simulationLength)

    return Node(gridIndex, traj, motionCommand[0], motionCommand[1], cost, index(currentNode))

def reedsSheppNode(currentNode, goalNode, mapParameters):

    # Get x, y, yaw of currentNode and goalNode
    startX, startY, startYaw = currentNode.traj[-1][0], currentNode.traj[-1][1], currentNode.traj[-1][2]
    goalX, goalY, goalYaw = goalNode.traj[-1][0], goalNode.traj[-1][1], goalNode.traj[-1][2]

    # Instantaneous Radius of Curvature
    radius = math.tan(Car.maxSteerAngle)/Car.wheelBase

    #  Find all possible reeds-shepp paths between current and goal node
    reedsSheppPaths = rsCurve.calc_all_paths(startX, startY, startYaw, goalX, goalY, goalYaw, radius, 1)

    # Check if reedsSheppPaths is empty
    if not reedsSheppPaths:
        return None

    # Find path with lowest cost considering non-holonomic constraints
    costQueue = heapdict()
    for path in reedsSheppPaths:
        costQueue[path] = reedsSheppCost(currentNode, path)

    # Find first path in priority queue that is collision free
    while len(costQueue)!=0:
        path = costQueue.popitem()[0]
        traj=[]
        traj = [[path.x[k],path.y[k],path.yaw[k]] for k in range(len(path.x))]
        if not collision(traj, mapParameters):
            cost = reedsSheppCost(currentNode, path)
            return Node(goalNode.gridIndex ,traj, None, None, cost, index(currentNode))
            
    return None

def isValid(traj, gridIndex, mapParameters):

    # Check if Node is out of map bounds
    if gridIndex[0]<=mapParameters.mapMinX or gridIndex[0]>=mapParameters.mapMaxX or \
       gridIndex[1]<=mapParameters.mapMinY or gridIndex[1]>=mapParameters.mapMaxY:
        return False

    # Check if Node is colliding with an obstacle
    if collision(traj, mapParameters):
        return False
    return True

def collision(traj, mapParameters):

    carRadius = (Car.axleToFront + Car.axleToBack)/2 + 1
    dl = (Car.axleToFront - Car.axleToBack)/2
    for i in traj:
        cx = i[0] + dl * math.cos(i[2])
        cy = i[1] + dl * math.sin(i[2])
        pointsInObstacle = mapParameters.ObstacleKDTree.query_ball_point([cx, cy], carRadius)

        if not pointsInObstacle:
            continue

        for p in pointsInObstacle:
            xo = mapParameters.obstacleX[p] - cx
            yo = mapParameters.obstacleY[p] - cy
            dx = xo * math.cos(i[2]) + yo * math.sin(i[2])
            dy = -xo * math.sin(i[2]) + yo * math.cos(i[2])

            if abs(dx) < carRadius and abs(dy) < Car.width / 2 + 1:
                return True

    return False

def reedsSheppCost(currentNode, path):

    # Previos Node Cost
    cost = currentNode.cost

    # Distance cost
    for i in path.lengths:
        if i >= 0:
            cost += 1
        else:
            cost += abs(i) * Cost.reverse

    # Direction change cost
    for i in range(len(path.lengths)-1):
        if path.lengths[i] * path.lengths[i+1] < 0:
            cost += Cost.directionChange

    # Steering Angle Cost
    for i in path.ctypes:
        # Check types which are not straight line
        if i!="S":
            cost += Car.maxSteerAngle * Cost.steerAngle

    # Steering Angle change cost
    turnAngle=[0.0 for _ in range(len(path.ctypes))]
    for i in range(len(path.ctypes)):
        if path.ctypes[i] == "R":
            turnAngle[i] = - Car.maxSteerAngle
        if path.ctypes[i] == "WB":
            turnAngle[i] = Car.maxSteerAngle

    for i in range(len(path.lengths)-1):
        cost += abs(turnAngle[i+1] - turnAngle[i]) * Cost.steerAngleChange

    return cost

def simulatedPathCost(currentNode, motionCommand, simulationLength):

    # Previos Node Cost
    cost = currentNode.cost

    # Distance cost
    if motionCommand[1] == 1:
        cost += simulationLength 
    else:
        cost += simulationLength * Cost.reverse

    # Direction change cost
    if currentNode.direction != motionCommand[1]:
        cost += Cost.directionChange

    # Steering Angle Cost
    cost += motionCommand[0] * Cost.steerAngle

    # Steering Angle change cost
    cost += abs(motionCommand[0] - currentNode.steeringAngle) * Cost.steerAngleChange

    return cost

def eucledianCost(holonomicMotionCommand):
    # Compute Eucledian Distance between two nodes
    return math.hypot(holonomicMotionCommand[0], holonomicMotionCommand[1])

def holonomicNodeIndex(HolonomicNode):
    # Index is a tuple consisting grid index, used for checking if two nodes are near/same
    return tuple([HolonomicNode.gridIndex[0], HolonomicNode.gridIndex[1]])

def obstaclesMap(obstacleX, obstacleY, xyResolution):

    # Compute Grid Index for obstacles
    obstacleX = [round(x / xyResolution) for x in obstacleX]
    obstacleY = [round(y / xyResolution) for y in obstacleY]

    # Set all Grid locations to No Obstacle
    obstacles =[[False for i in range(max(obstacleY))]for i in range(max(obstacleX))]

    # Set Grid Locations with obstacles to True
    for x in range(max(obstacleX)):
        for y in range(max(obstacleY)):
            for i, j in zip(obstacleX, obstacleY):
                if math.hypot(i-x, j-y) <= 1/2:
                    obstacles[i][j] = True
                    break

    return obstacles

def holonomicNodeIsValid(neighbourNode, obstacles, mapParameters):

    # Check if Node is out of map bounds
    if neighbourNode.gridIndex[0]<= mapParameters.mapMinX or \
       neighbourNode.gridIndex[0]>= mapParameters.mapMaxX or \
       neighbourNode.gridIndex[1]<= mapParameters.mapMinY or \
       neighbourNode.gridIndex[1]>= mapParameters.mapMaxY:
        return False

    # Check if Node on obstacle
    if obstacles[neighbourNode.gridIndex[0]][neighbourNode.gridIndex[1]]:
        return False

    return True

def holonomicCostsWithObstacles(goalNode, mapParameters):

    gridIndex = [round(goalNode.traj[-1][0]/mapParameters.xyResolution), round(goalNode.traj[-1][1]/mapParameters.xyResolution)]
    gNode =HolonomicNode(gridIndex, 0, tuple(gridIndex))

    obstacles = obstaclesMap(mapParameters.obstacleX, mapParameters.obstacleY, mapParameters.xyResolution)

    holonomicMotionCommand = holonomicMotionCommands()

    openSet = {holonomicNodeIndex(gNode): gNode}
    closedSet = {}

    priorityQueue =[]
    heapq.heappush(priorityQueue, (gNode.cost, holonomicNodeIndex(gNode)))

    while True:
        if not openSet:
            break

        _, currentNodeIndex = heapq.heappop(priorityQueue)
        currentNode = openSet[currentNodeIndex]
        openSet.pop(currentNodeIndex)
        closedSet[currentNodeIndex] = currentNode

        for i in range(len(holonomicMotionCommand)):
            neighbourNode = HolonomicNode([currentNode.gridIndex[0] + holonomicMotionCommand[i][0],\
                                      currentNode.gridIndex[1] + holonomicMotionCommand[i][1]],\
                                      currentNode.cost + eucledianCost(holonomicMotionCommand[i]), currentNodeIndex)

            if not holonomicNodeIsValid(neighbourNode, obstacles, mapParameters):
                continue

            neighbourNodeIndex = holonomicNodeIndex(neighbourNode)

            if neighbourNodeIndex not in closedSet:            
                if neighbourNodeIndex in openSet:
                    if neighbourNode.cost < openSet[neighbourNodeIndex].cost:
                        openSet[neighbourNodeIndex].cost = neighbourNode.cost
                        openSet[neighbourNodeIndex].parentIndex = neighbourNode.parentIndex
                        # heapq.heappush(priorityQueue, (neighbourNode.cost, neighbourNodeIndex))
                else:
                    openSet[neighbourNodeIndex] = neighbourNode
                    heapq.heappush(priorityQueue, (neighbourNode.cost, neighbourNodeIndex))

    holonomicCost = [[np.inf for i in range(max(mapParameters.obstacleY))]for i in range(max(mapParameters.obstacleX))]

    for nodes in closedSet.values():
        holonomicCost[nodes.gridIndex[0]][nodes.gridIndex[1]]=nodes.cost

    return holonomicCost

def map():
    obstacleX, obstacleY = [], []

    # Scale down the original dimensions (5000, 3000) to fit the grid
    scale = 10  # This means 1 grid unit = 100 pixels in original map
    map_width = 500  # 5000/100
    map_height = 300  # 3000/100

    # Outer straight lines
    # bottom horizontal line
    for i in range(132, map_width - 132):
        obstacleX.append(i)
        obstacleY.append(0)

    # top horizontal line
    for i in range(132, map_width - 132):
        obstacleX.append(i)
        obstacleY.append(map_height)

    # Left vertical line
    for i in range(132, map_height - 132):
        obstacleX.append(0)
        obstacleY.append(i)

    # Right vertical line
    for i in range(132, map_height - 132):
        obstacleX.append(map_width)
        obstacleY.append(i)

    # Inner straight lines
    # Top inner horizontal line
    for i in range(60, 145):
        obstacleX.append(i)
        obstacleY.append(120)
    for i in range(205, 220):
        obstacleX.append(i)
        obstacleY.append(120)

    for i in range(280, 295):
        obstacleX.append(i)
        obstacleY.append(120)
    for i in range(325, 440):
        obstacleX.append(i)
        obstacleY.append(120)

    # chuong ngang 600x312
    for i in range(145, 205):
        obstacleX.append(i)
        obstacleY.append(88)
    for i in range(88, 120):
        obstacleX.append(145)
        obstacleY.append(i)
    for i in range(88, 120):
        obstacleX.append(205)
        obstacleY.append(i)

    # chuong doc 300x550
    for i in range(295, 325):
        obstacleX.append(i)
        obstacleY.append(65)
    for i in range(65, 120):
        obstacleX.append(295)
        obstacleY.append(i)
    for i in range(65, 120):
        obstacleX.append(325)
        obstacleY.append(i)

    # Bottom inner horizontal line
    for i in range(60, 220):
        obstacleX.append(i)
        obstacleY.append(180)
    for i in range(280, 440):
        obstacleX.append(i)
        obstacleY.append(180)

    # Left inner vertical line
    for i in range(60, 120):
        obstacleX.append(220)
        obstacleY.append(i)
    for i in range(180, 240):
        obstacleX.append(220)
        obstacleY.append(i)

    # # Right inner vertical line
    for i in range(60, 120):
        obstacleX.append(280)
        obstacleY.append(i)
    for i in range(180, 240):
        obstacleX.append(280)
        obstacleY.append(i)

    # Function to add corner points
    def add_corner_points(radius, center_x, center_y, start_angle, end_angle):
        for angle in range(start_angle, end_angle + 1):
            x = radius * math.cos(math.radians(angle)) + center_x
            y = radius * math.sin(math.radians(angle)) + center_y
            obstacleX.append(round(x))
            obstacleY.append(round(y))

    # Outer corners
    outer_radius = 132
    add_corner_points(outer_radius, outer_radius, outer_radius, 180, 270)
    add_corner_points(outer_radius, map_width - outer_radius, outer_radius, 270, 360)
    add_corner_points(outer_radius, outer_radius, map_height - outer_radius, 90, 180)
    add_corner_points(outer_radius, map_width - outer_radius, map_height - outer_radius, 0, 90)

    # Inner corners
    # inner_radius = 66
    # add_corner_points(inner_radius, outer_radius, outer_radius, 180, 270)
    # add_corner_points(inner_radius, map_width - outer_radius, outer_radius, 270, 360)
    # add_corner_points(inner_radius, outer_radius, map_height - outer_radius, 90, 180)
    # add_corner_points(inner_radius, map_width - outer_radius, map_height - outer_radius, 0, 90)
    # Build Map
    # obstacleX, obstacleY = [], []
    #
    # for i in range(51):
    #     obstacleX.append(i)
    #     obstacleY.append(0)
    #
    # for i in range(51):
    #     obstacleX.append(0)
    #     obstacleY.append(i)
    #
    # for i in range(51):
    #     obstacleX.append(i)
    #     obstacleY.append(50)
    #
    # for i in range(51):
    #     obstacleX.append(50)
    #     obstacleY.append(i)
    #
    # for i in range(10,20):
    #     obstacleX.append(i)
    #     obstacleY.append(30)
    #
    # for i in range(30,51):
    #     obstacleX.append(i)
    #     obstacleY.append(30)
    #
    # for i in range(0,31):
    #     obstacleX.append(20)
    #     obstacleY.append(i)
    #
    # for i in range(0,31):
    #     obstacleX.append(30)
    #     obstacleY.append(i)
    #
    # for i in range(40,50):
    #     obstacleX.append(15)
    #     obstacleY.append(i)
    #
    # for i in range(25,40):
    #     obstacleX.append(i)
    #     obstacleY.append(35)

    # Parking Map
    # for i in range(51):
    #     obstacleX.append(i)
    #     obstacleY.append(0)

    # for i in range(51):
    #     obstacleX.append(0)
    #     obstacleY.append(i)

    # for i in range(51):
    #     obstacleX.append(i)
    #     obstacleY.append(50)

    # for i in range(51):
    #     obstacleX.append(50)
    #     obstacleY.append(i)

    # for i in range(51):
    #     obstacleX.append(i)
    #     obstacleY.append(40)

    # for i in range(0,20):
    #     obstacleX.append(i)
    #     obstacleY.append(30) 

    # for i in range(29,51):
    #     obstacleX.append(i)
    #     obstacleY.append(30) 

    # for i in range(24,30):
    #     obstacleX.append(19)
    #     obstacleY.append(i) 

    # for i in range(24,30):
    #     obstacleX.append(29)
    #     obstacleY.append(i) 

    # for i in range(20,29):
    #     obstacleX.append(i)
    #     obstacleY.append(24)

    return obstacleX, obstacleY

def backtrack(startNode, goalNode, closedSet, plt):

    # Goal Node data
    startNodeIndex= index(startNode)
    currentNodeIndex = goalNode.parentIndex
    currentNode = closedSet[currentNodeIndex]
    x=[]
    y=[]
    yaw=[]

    # Iterate till we reach start node from goal node
    while currentNodeIndex != startNodeIndex:
        a, b, c = zip(*currentNode.traj)
        x += a[::-1] 
        y += b[::-1] 
        yaw += c[::-1]
        currentNodeIndex = currentNode.parentIndex
        currentNode = closedSet[currentNodeIndex]
    return x[::-1], y[::-1], yaw[::-1]

def run(s, g, mapParameters, plt):

    # Compute Grid Index for start and Goal node
    sGridIndex = [round(s[0] / mapParameters.xyResolution), \
                  round(s[1] / mapParameters.xyResolution), \
                  round(s[2]/mapParameters.yawResolution)]
    gGridIndex = [round(g[0] / mapParameters.xyResolution), \
                  round(g[1] / mapParameters.xyResolution), \
                  round(g[2]/mapParameters.yawResolution)]

    # Generate all Possible motion commands to car
    motionCommand = motionCommands()

    # Create start and end Node
    startNode = Node(sGridIndex, [s], 0, 1, 0 , tuple(sGridIndex))
    goalNode = Node(gGridIndex, [g], 0, 1, 0, tuple(gGridIndex))

    # Find Holonomic Heuristric
    holonomicHeuristics = holonomicCostsWithObstacles(goalNode, mapParameters)

    # Add start node to open Set
    openSet = {index(startNode):startNode}
    closedSet = {}

    # Create a priority queue for acquiring nodes based on their cost's
    costQueue = heapdict()

    # Add start mode into priority queue
    costQueue[index(startNode)] = max(startNode.cost , Cost.hybridCost * holonomicHeuristics[startNode.gridIndex[0]][startNode.gridIndex[1]])
    counter = 0

    # Run loop while path is found or open set is empty
    while True:
        counter +=1
        # Check if openSet is empty, if empty no solution available
        if not openSet:
            return None

        # Get first node in the priority queue
        currentNodeIndex = costQueue.popitem()[0]
        currentNode = openSet[currentNodeIndex]

        # Revove currentNode from openSet and add it to closedSet
        openSet.pop(currentNodeIndex)
        closedSet[currentNodeIndex] = currentNode


        # Get Reed-Shepp Node if available
        rSNode = reedsSheppNode(currentNode, goalNode, mapParameters)

        # Id Reeds-Shepp Path is found exit
        if rSNode:
            closedSet[index(rSNode)] = rSNode
            break

        # USED ONLY WHEN WE DONT USE REEDS-SHEPP EXPANSION OR WHEN START = GOAL
        if currentNodeIndex == index(goalNode):
            print("Path Found")
            print(currentNode.traj[-1])
            break

        # Get all simulated Nodes from current node
        for i in range(len(motionCommand)):
            simulatedNode = kinematicSimulationNode(currentNode, motionCommand[i], mapParameters)

            # Check if path is within map bounds and is collision free
            if not simulatedNode:
                continue

            # Draw Simulated Node
            x,y,z =zip(*simulatedNode.traj)
            plt.plot(x, y, linewidth=0.3, color='g')

            # Check if simulated node is already in closed set
            simulatedNodeIndex = index(simulatedNode)
            if simulatedNodeIndex not in closedSet: 

                # Check if simulated node is already in open set, if not add it open set as well as in priority queue
                if simulatedNodeIndex not in openSet:
                    openSet[simulatedNodeIndex] = simulatedNode
                    costQueue[simulatedNodeIndex] = max(simulatedNode.cost , Cost.hybridCost * holonomicHeuristics[simulatedNode.gridIndex[0]][simulatedNode.gridIndex[1]])
                else:
                    if simulatedNode.cost < openSet[simulatedNodeIndex].cost:
                        openSet[simulatedNodeIndex] = simulatedNode
                        costQueue[simulatedNodeIndex] = max(simulatedNode.cost , Cost.hybridCost * holonomicHeuristics[simulatedNode.gridIndex[0]][simulatedNode.gridIndex[1]])
    
    # Backtrack
    x, y, yaw = backtrack(startNode, goalNode, closedSet, plt)

    return x, y, yaw

def drawCar(x, y, yaw, color='black'):
    car = np.array([[-Car.axleToBack, -Car.axleToBack, Car.axleToFront, Car.axleToFront, -Car.axleToBack],
                    [Car.width / 2, -Car.width / 2, -Car.width / 2, Car.width / 2, Car.width / 2]])

    rotationZ = np.array([[math.cos(yaw), -math.sin(yaw)],
                     [math.sin(yaw), math.cos(yaw)]])
    car = np.dot(rotationZ, car)
    car += np.array([[x], [y]])
    plt.plot(car[0, :], car[1, :], color)

def main():

    # Set Start, Goal x, y, theta
    data, addr = sock.recvfrom(1024)
    decoded_data = data.decode().strip()
    init_x, init_y, init_yaw = map(float, decoded_data.split('_'))  # Tách dữ liệu thành ba thông số: yaw ở radian
    s = [init_x, init_y, init_yaw]  # Lưu vào danh sách và chuyển yaw sang radian
    #s = [310, 120, np.deg2rad(90)]
    g = [310, 95, np.deg2rad(90)]
    # s = [10, 35, np.deg2rad(0)]
    # g = [22, 28, np.deg2rad(0)]

    # Get Obstacle Map
    obstacleX, obstacleY = map()

    # Calculate map Paramaters
    mapParameters = calculateMapParameters(obstacleX, obstacleY, 4, np.deg2rad(15.0))

    # Run Hybrid A*
    x, y, yaw = run(s, g, mapParameters, plt)

    # Draw Start, Goal Location Map and Path
    # plt.arrow(s[0], s[1], 1*math.cos(s[2]), 1*math.sin(s[2]), width=.1)
    # plt.arrow(g[0], g[1], 1*math.cos(g[2]), 1*math.sin(g[2]), width=.1)
    # plt.xlim(min(obstacleX), max(obstacleX)) 
    # plt.ylim(min(obstacleY), max(obstacleY))
    # plt.plot(obstacleX, obstacleY, "sk")
    # plt.plot(x, y, linewidth=2, color='r', zorder=0)
    # plt.title("Hybrid A*")


    # Draw Path, Map and Car Footprint
    # plt.plot(x, y, linewidth=1.5, color='r', zorder=0)
    # plt.plot(obstacleX, obstacleY, "sk")
    # for k in np.arange(0, len(x), 2):
    #     plt.xlim(min(obstacleX), max(obstacleX)) 
    #     plt.ylim(min(obstacleY), max(obstacleY))
    #     drawCar(x[k], y[k], yaw[k])
    #     plt.arrow(x[k], y[k], 1*math.cos(yaw[k]), 1*math.sin(yaw[k]), width=.1)
    #     plt.title("Hybrid A*")

    # Draw Animated Car
    for k in range(len(x)):
        print(f"Step {k}: x = {x[k]:.2f}, y = {y[k]:.2f}, yaw = {math.degrees(yaw[k]):.2f} degrees")
        while True:
            data, addr = sock.recvfrom(1024)  # Nhận dữ liệu từ client
            decoded_data = data.decode().strip()

            try:
                Car_x, Car_y, Car_yaw = map(float, decoded_data.split('_'))  # Tách dữ liệu thành ba thông số: yaw ở radian
            except ValueError:
                print(f"Dữ liệu không hợp lệ: {decoded_data}")
                continue  # Bỏ qua nếu format không đúng
            direct_ang = np.arctan2(y[k]-Car_y,x[k]-Car_x)
            direct_diff = np.degrees(rsCurve.pi_2_pi(direct_ang - yaw[k]))
            yaw_diff = np.degrees(rsCurve.pi_2_pi(yaw[k] - Car_yaw))
            if -90 <= direct_diff <= 90:
                ser.write("M-6000 ".encode())
                if yaw_diff < -1:
                    ser.write("S110 ".encode())
                elif yaw_diff > 1:
                    ser.write("S50 ".encode())
                else:
                    ser.write("S80 ".encode())
            elif direct_diff > 90 or direct_diff < -90:
                ser.write("M+6000 ".encode())
                if yaw_diff < -1:
                    ser.write("S50 ".encode())
                elif yaw_diff > 1:
                    ser.write("S110 ".encode())
                else:
                    ser.write("S80 ".encode())
            time.sleep(0.5)
            if k < len(yaw) - 1:
                direct_ang_next = np.arctan2(y[k+1] - y[k], x[k+1] - x[k])
                direct_diff_next = np.degrees(direct_ang_next-direct_ang)
                if direct_diff_next >= 90:
                    ser.write("M-0 ".encode())
            x_diff = Car_x - x[k]
            y_diff = Car_y - y[k]
            if -1 <= x_diff <= 1 and -1 <= y_diff <= 1 and -1 <= yaw_diff <=1:
                break

    ser.write("M-0 ".encode())
    ser.write("S80 ".encode())

    #     plt.cla()
    #     plt.xlim(min(obstacleX), max(obstacleX))
    #     plt.ylim(min(obstacleY), max(obstacleY))
    #     plt.scatter(obstacleX, obstacleY, s=1, color='black')
    #     plt.plot(x, y, linewidth=1, color='r', zorder=0)
    #     drawCar(x[k], y[k], yaw[k])
    #     plt.arrow(x[k], y[k], 1*math.cos(yaw[k]), 1*math.sin(yaw[k]), width=.1)
    #     plt.title("Hybrid A*")
    #     plt.pause(0.5)
    #
    # plt.show()

if __name__ == '__main__':
    main()
