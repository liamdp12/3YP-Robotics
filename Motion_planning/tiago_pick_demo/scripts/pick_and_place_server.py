#! /usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2016 PAL Robotics SL. All Rights Reserved
#
# Permission to use, copy, modify, and/or distribute this software for
# any purpose with or without fee is hereby granted, provided that the
# above copyright notice and this permission notice appear in all
# copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
#
# Author:
#   * Sam Pfeiffer
#   * Job van Dieten
#   * Jordi Pages
import sys
import rospy
from spherical_grasps_server import SphericalGrasps
from actionlib import SimpleActionClient, SimpleActionServer
import moveit_commander
from moveit_commander import PlanningSceneInterface, MoveGroupCommander
from moveit_msgs.msg import Grasp, PickupAction, PickupGoal, PickupResult, MoveItErrorCodes
from moveit_msgs.msg import PlaceAction, PlaceGoal, PlaceResult, PlaceLocation
from geometry_msgs.msg import Pose, PoseStamped, PoseArray, Vector3Stamped, Vector3, Quaternion
from tiago_pick_demo.msg import PickUpPoseAction, PickUpPoseGoal, PickUpPoseResult, PickUpPoseFeedback
from moveit_msgs.srv import GetPlanningScene, GetPlanningSceneRequest, GetPlanningSceneResponse
from std_srvs.srv import Empty, EmptyRequest
from copy import deepcopy
from random import shuffle
import copy

import tf2_ros
import tf2_geometry_msgs
from apriltag_ros.msg import AprilTagDetectionArray

moveit_error_dict = {}
for name in MoveItErrorCodes.__dict__.keys():
	if not name[:1] == '_':
		code = MoveItErrorCodes.__dict__[name]
		moveit_error_dict[code] = name


def createPickupGoal(group="arm_torso", target="part",
					 grasp_pose=PoseStamped(),
					 possible_grasps=[],
					 links_to_allow_contact=None):
	""" Create a PickupGoal with the provided data"""
	pug = PickupGoal()
	pug.target_name = target
	pug.group_name = group
	pug.possible_grasps.extend(possible_grasps)
	pug.allowed_planning_time = 35.0
	pug.planning_options.planning_scene_diff.is_diff = True
	pug.planning_options.planning_scene_diff.robot_state.is_diff = True
	pug.planning_options.plan_only = False
	pug.planning_options.replan = True
	pug.planning_options.replan_attempts = 1  # 10
	pug.allowed_touch_objects = []
	pug.attached_object_touch_links = ['<octomap>']
	pug.attached_object_touch_links.extend(links_to_allow_contact)

	return pug


def createPlaceGoal(place_pose,
					place_locations,
					group="arm_torso",
					target="part",
					links_to_allow_contact=None):
	"""Create PlaceGoal with the provided data"""
	placeg = PlaceGoal()
	placeg.group_name = group
	placeg.attached_object_name = target
	placeg.place_locations = place_locations
	placeg.allowed_planning_time = 15.0
	placeg.planning_options.planning_scene_diff.is_diff = True
	placeg.planning_options.planning_scene_diff.robot_state.is_diff = True
	placeg.planning_options.plan_only = False
	placeg.planning_options.replan = True
	placeg.planning_options.replan_attempts = 1
	placeg.allowed_touch_objects = ['<octomap>']
	placeg.allowed_touch_objects.extend(links_to_allow_contact)

	return placeg

class PickAndPlaceServer(object):
	def __init__(self):
		rospy.loginfo("Initalizing PickAndPlaceServer...")
		self.sg = SphericalGrasps()
		rospy.loginfo("Connecting to pickup AS")
		self.pickup_ac = SimpleActionClient('/pickup', PickupAction)	# Create an action client for the '/pickup' action server -> line 213
		self.pickup_ac.wait_for_server()
		rospy.loginfo("Succesfully connected.")
		rospy.loginfo("Connecting to place AS")
		self.place_ac = SimpleActionClient('/place', PlaceAction)
		self.place_ac.wait_for_server()
		rospy.loginfo("Succesfully connected.")
		self.scene = PlanningSceneInterface()
		rospy.loginfo("Connecting to /get_planning_scene service")
		self.scene_srv = rospy.ServiceProxy(
			'/get_planning_scene', GetPlanningScene)
		self.scene_srv.wait_for_service()
		rospy.loginfo("Connected.")
		rospy.loginfo("Connecting to clear octomap service...")
		self.clear_octomap_srv = rospy.ServiceProxy(
			'/clear_octomap', Empty)
		self.clear_octomap_srv.wait_for_service()
		rospy.loginfo("Connected!")

		# Get the object size
		self.object_height = rospy.get_param('~object_height')
		self.object_width = rospy.get_param('~object_width')
		self.object_depth = rospy.get_param('~object_depth')

		# Get the links of the end effector exclude from collisions
		self.links_to_allow_contact = rospy.get_param('~links_to_allow_contact', None)
		if self.links_to_allow_contact is None:
			rospy.logwarn("Didn't find any links to allow contacts... at param ~links_to_allow_contact")
		else:
			rospy.loginfo("Found links to allow contacts: " + str(self.links_to_allow_contact))
#############################################################################
		self.pick_as = SimpleActionServer(
			'/pickup_pose', PickUpPoseAction,
			execute_cb=self.pick_cb, auto_start=False)	# A callback function to execute when the action is called. In this case, the callback is self.pick_cb.
		self.pick_as.start()		# This starts the action server and allows it to begin accepting goals.

		self.place_as = SimpleActionServer(
			'/place_pose', PickUpPoseAction,
			execute_cb=self.place_cb, auto_start=False)
		self.place_as.start()
#############################################################################
	def pick_cb(self, goal):
		"""
		:type goal: PickUpPoseGoal
		"""
		error_code = self.grasp_object(goal.object_pose)
		p_res = PickUpPoseResult()
		p_res.error_code = error_code
		if error_code != 1:
			self.pick_as.set_aborted(p_res)
		else:
			self.pick_as.set_succeeded(p_res)	# Acrion server sends a result to the client
		
		#self.clean_table(goal.object_pose)

	def place_cb(self, goal):
		"""
		:type goal: PickUpPoseGoal
		"""
		error_code = self.place_object(goal.object_pose)
		p_res = PickUpPoseResult()
		p_res.error_code = error_code
		if error_code != 1:
			self.place_as.set_aborted(p_res)
		else:
			self.place_as.set_succeeded(p_res)

	def wait_for_planning_scene_object(self, object_name='part'):
		rospy.loginfo(
			"Waiting for object '" + object_name + "'' to appear in planning scene...")
		gps_req = GetPlanningSceneRequest()
		gps_req.components.components = gps_req.components.WORLD_OBJECT_NAMES
		
		part_in_scene = False
		while not rospy.is_shutdown() and not part_in_scene:
			# This call takes a while when rgbd sensor is set
			gps_resp = self.scene_srv.call(gps_req)
			# check if 'part' is in the answer
			for collision_obj in gps_resp.scene.world.collision_objects:
				if collision_obj.id == object_name:
					part_in_scene = True
					break
			else:
				rospy.sleep(1.0)

		rospy.loginfo("'" + object_name + "'' is in scene!")

	def process_info(self, msg):
		# tag id: [table_height, table_width, table_depth]
		id_to_info = {0:[.6, 3, .8], 1:[.6, 1, 1]}
		self.table_detected = False
		
		for detection in msg.detections:
			tag_id = detection.id[0]
			if (tag_id == 1): # to change, to be sent by task planning
				#rospy.loginfo(f"apriltag id: {tag_id}")
				self.tag_info = id_to_info[tag_id]
				#rospy.loginfo(f"height: {tag_info[0]}")
				#rospy.loginfo(f"width: {tag_info[1]}")
				#rospy.loginfo(f"depth: {tag_info[2]}")
				# to add info on height, width, depth
				
				tag_pose_relative_to_camera = detection.pose.pose.pose
				# transform to PoseStamped
				tag_pose_relative_to_camera_stamped = PoseStamped()
				tag_pose_relative_to_camera_stamped.header.stamp = rospy.Time.now()
				tag_pose_relative_to_camera_stamped.header.frame_id = "xtion_rgb_optical_frame"
				tag_pose_relative_to_camera_stamped.pose = tag_pose_relative_to_camera

				#rospy.loginfo(f"Pose relative to camera: {tag_pose_relative_to_camera}") # debugging
				
				try:
					transform = self.tf_buffer.lookup_transform("base_footprint", "xtion_rgb_optical_frame", rospy.Time(0), rospy.Duration(1.0))
					rospy.loginfo(f"transform: {transform}")
				except Exception as e:
					rospy.loginfo("failed to lookup transform")
					rospy.loginfo(e)

				self.tag_pose_relative_to_base_stamped = tf2_geometry_msgs.do_transform_pose(tag_pose_relative_to_camera_stamped, transform)
				tag_pose_relative_to_base = self.tag_pose_relative_to_base_stamped.pose
				self.table_detected = True

				#rospy.loginfo(f"transformed pose: {tag_pose_relative_to_base}")
				

	def grasp_object(self, object_pose):
		rospy.loginfo("Removing any previous 'part' object")
		self.scene.remove_attached_object("arm_tool_link")
		self.scene.remove_world_object("part")
		self.scene.remove_world_object("table")
		rospy.loginfo("Clearing octomap")
		self.clear_octomap_srv.call(EmptyRequest())
		rospy.sleep(2.0)  # Removing is fast
		rospy.loginfo("Adding new 'part' object")

		rospy.loginfo("Object pose: %s", object_pose.pose)
		object_pose.pose.position.z += 0.016
		
		#Add object description in scene
		self.scene.add_box("part", object_pose, (self.object_depth, self.object_width, self.object_height))

		rospy.loginfo("Second%s", object_pose.pose)
		table_pose = copy.deepcopy(object_pose)
		###################################################### Modify this table size ########################################
		rospy.init_node("apriltag_detection_lister", anonymous=True)
		self.tf_buffer = tf2_ros.Buffer()
		self.tf_listener = tf2_ros.TransformListener(tf_buffer)
		rospy.Subscriber("/tag_detections", AprilTagDetectionArray,self.process_info)

		while (not self.table_detected):
			rospy.sleep(1.0)

		#define a virtual table below the object
		table_height = object_pose.pose.position.z - 0.016 - self.object_height/2 + 0.015
		table_width  = self.tag_info[1]
		table_depth  = self.tag_info[2]
		table_pose.pose.position.x = self.tag_pose_relative_to_base_stamped.pose.position.x
		table_pose.pose.position.z = table_height/2

		self.scene.add_box("table", table_pose, (table_depth, table_width, table_height))		# What does this do? ############

		# # We need to wait for the object part to appear
		self.wait_for_planning_scene_object()
		self.wait_for_planning_scene_object("table")

		# compute grasps
		possible_grasps = self.sg.create_grasps_from_object_pose(object_pose)
		self.pickup_ac	
		goal = createPickupGoal(
			"arm_torso", "part", object_pose, possible_grasps, self.links_to_allow_contact)

		rospy.loginfo("Sending goal")
		self.pickup_ac.send_goal(goal)			# Send the goal to the action server (/pickup) and wait for the result, but where is the server?
		rospy.loginfo("Waiting for result")
		self.pickup_ac.wait_for_result()		
		result = self.pickup_ac.get_result()
		rospy.logdebug("Using torso result: " + str(result))
		rospy.loginfo(
			"Pick result: " +
		str(moveit_error_dict[result.error_code.val]))

		return result.error_code.val	# Return the error code

		

	def place_object(self, object_pose):
		rospy.loginfo("Clearing octomap")		## why clean octomap?
		self.clear_octomap_srv.call(EmptyRequest())
		possible_placings = self.sg.create_placings_from_object_pose(
			object_pose)
		# Try only with arm
		rospy.loginfo("Trying to place using only arm")
		goal = createPlaceGoal(
			object_pose, possible_placings, "arm", "part", self.links_to_allow_contact)
		rospy.loginfo("Sending goal")
		self.place_ac.send_goal(goal)
		rospy.loginfo("Waiting for result")

		self.place_ac.wait_for_result()
		result = self.place_ac.get_result()
		rospy.loginfo(str(moveit_error_dict[result.error_code.val]))

		if str(moveit_error_dict[result.error_code.val]) != "SUCCESS":
			rospy.loginfo(
				"Trying to place with arm and torso")
			# Try with arm and torso
			goal = createPlaceGoal(
				object_pose, possible_placings, "arm_torso", "part", self.links_to_allow_contact)
			rospy.loginfo("Sending goal")
			self.place_ac.send_goal(goal)
			rospy.loginfo("Waiting for result")

			self.place_ac.wait_for_result()
			result = self.place_ac.get_result()
			rospy.logerr(str(moveit_error_dict[result.error_code.val]))
		
		# print result
		rospy.loginfo(
			"Result: " +
			str(moveit_error_dict[result.error_code.val]))
		rospy.loginfo("Removing previous 'part' object")
		self.scene.remove_world_object("part")

		return result.error_code.val


if __name__ == '__main__':
	rospy.init_node('pick_and_place_server')
	paps = PickAndPlaceServer()
	rospy.spin()
