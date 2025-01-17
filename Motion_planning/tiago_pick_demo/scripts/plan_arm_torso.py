#! /usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import rospy
import moveit_commander
from moveit_commander import MoveGroupCommander
from geometry_msgs.msg import PoseStamped
from tf.transformations import quaternion_from_euler

def move(args):
    moveit_commander.roscpp_initialize(sys.argv)
    rospy.init_node('plan_arm_torso_ik', anonymous=True)

    # if len(args) < 7:
    #     rospy.loginfo("Usage: plan_arm_torso_ik  x y z  r p y")
    #     rospy.loginfo("where the list of arguments specify the target pose of /arm_tool_link expressed in /base_footprint")
    #     return

    # Default values
    default_values = [0.4, 0, 0.26, -0.011, 1.57, 0.037]
    
    if args is None or len(args) < 7:
        rospy.loginfo("Using default values for the target pose of /arm_tool_link expressed in /base_footprint")
        args = default_values

    goal_pose = PoseStamped()
    goal_pose.header.frame_id = "base_footprint"
    goal_pose.pose.position.x = float(args[0])
    goal_pose.pose.position.y = float(args[1])
    goal_pose.pose.position.z = float(args[2])
    q = quaternion_from_euler(float(args[3]), float(args[4]), float(args[5]))
    goal_pose.pose.orientation.x = q[0]
    goal_pose.pose.orientation.y = q[1]
    goal_pose.pose.orientation.z = q[2]
    goal_pose.pose.orientation.w = q[3]

    group_arm_torso = MoveGroupCommander("arm_torso")
    group_arm_torso.set_planner_id("SBLkConfigDefault")
    group_arm_torso.set_pose_reference_frame("base_footprint")
    group_arm_torso.set_pose_target(goal_pose)

    rospy.loginfo("Planning to move %s to a target pose expressed in %s" % 
                  (group_arm_torso.get_end_effector_link(), group_arm_torso.get_planning_frame()))

    group_arm_torso.set_start_state_to_current_state()
    group_arm_torso.set_max_velocity_scaling_factor(1.0)

    group_arm_torso.set_planning_time(5.0)
    success, plan, planning_time, _ = group_arm_torso.plan()

    if success:
        rospy.loginfo("Plan found in %s seconds" % planning_time)
    else:
        rospy.loginfo("Planning failed")


    start = rospy.Time.now()
    group_arm_torso.go()
    rospy.loginfo("Motion duration: %s seconds" % (rospy.Time.now() - start).to_sec())

    moveit_commander.roscpp_shutdown()

if __name__ == '__main__':
    move(sys.argv[1:])