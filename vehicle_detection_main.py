#!/usr/bin/python
# -*- coding: utf-8 -*-
# ----------------------------------------------
# --- Author         : Ahmet Ozlu
# --- Mail           : ahmetozlu93@gmail.com
# --- Date           : 27th January 2018
# ----------------------------------------------

# Imports
import asyncio
import base64
import concurrent.futures
import os
from typing import Union

import tensorflow.compat.v1 as tf
from gql import Client, gql
from gql.transport.requests import RequestsHTTPTransport

tf.disable_v2_behavior()
import cv2
import numpy as np
import csv

from hyperlpr import HyperLPR_plate_recognition
from packaging import version

# Object detection imports
from model.Vehicle import Vehicle
from gql.transport.aiohttp import AIOHTTPTransport
from utils import label_map_util
from utils import visualization_utils as vis_util

if version.parse(tf.__version__) < version.parse('1.4.0'):
    raise ImportError('Please upgrade your tensorflow installation to v1.4.* or later!'
                      )

# input video
cap = cv2.VideoCapture('sub-1504619634606.mp4')

# By default I use an "SSD with Mobilenet" model here. See the detection model zoo (https://github.com/tensorflow/models/blob/master/research/object_detection/g3doc/detection_model_zoo.md) for a list of other models that can be run out-of-the-box with varying speeds and accuracies.
# What model to download.
MODEL_NAME = 'ssd_mobilenet_v1_coco_2018_01_28'
MODEL_FILE = MODEL_NAME + '.tar.gz'
DOWNLOAD_BASE = \
    'http://download.tensorflow.org/models/object_detection/'

# Path to frozen detection graph. This is the actual model that is used for the object detection.
PATH_TO_CKPT = MODEL_NAME + '/frozen_inference_graph.pb'

# List of the strings that is used to add correct label for each box.
PATH_TO_LABELS = os.path.join('data', 'mscoco_label_map.pbtxt')

NUM_CLASSES = 90

# Download Model
# uncomment if you have not download the model yet
# Load a (frozen) Tensorflow model into memory.
detection_graph = tf.Graph()
with detection_graph.as_default():
    od_graph_def = tf.GraphDef()
    with tf.gfile.GFile(PATH_TO_CKPT, 'rb') as fid:
        serialized_graph = fid.read()
        od_graph_def.ParseFromString(serialized_graph)
        tf.import_graph_def(od_graph_def, name='')

# Loading label map
# Label maps map indices to category names, so that when our convolution network predicts 5, we know that this corresponds to airplane. Here I use internal utility functions, but anything that returns a dictionary mapping integers to appropriate string labels would be fine
label_map = label_map_util.load_labelmap(PATH_TO_LABELS)
categories = label_map_util.convert_label_map_to_categories(label_map,
                                                            max_num_classes=NUM_CLASSES, use_display_name=True)
category_index = label_map_util.create_category_index(categories)


# Helper code
def load_image_into_numpy_array(image):
    (im_width, im_height) = image.size
    return np.array(image.getdata()).reshape((im_height, im_width,
                                              3)).astype(np.uint8)


def draw_roi(counter, frame, pos) -> None:
    if counter == 1:
        cv2.line(frame, (0, pos), (640, pos), (0, 0xFF, 0), 5)
    else:
        cv2.line(frame, (0, pos), (640, pos), (0, 0, 0xFF), 5)
    cv2.putText(
        frame,
        'ROI Line',
        (545, pos - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 0, 0xFF),
        2,
        cv2.LINE_AA, )


def draw_info(frame, vehicle: Vehicle) -> None:
    if vehicle is None:
        return
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.rectangle(frame, (10, 275), (230, 337), (180, 132, 109), -1)

    cv2.putText(
        frame,
        'LAST PASSED VEHICLE INFO',
        (11, 290),
        font,
        0.5,
        (0xFF, 0xFF, 0xFF),
        1,
        cv2.FONT_HERSHEY_SIMPLEX,
    )
    cv2.putText(
        frame,
        '-Movement Direction: ' + vehicle.direction,
        (14, 302),
        font,
        0.4,
        (0xFF, 0xFF, 0xFF),
        1,
        cv2.FONT_HERSHEY_COMPLEX_SMALL,
    )
    cv2.putText(
        frame,
        '-Speed(km/h): ' + str(vehicle.speed),
        (14, 312),
        font,
        0.4,
        (0xFF, 0xFF, 0xFF),
        1,
        cv2.FONT_HERSHEY_COMPLEX_SMALL,
    )
    cv2.putText(
        frame,
        '-Color: ' + vehicle.color,
        (14, 322),
        font,
        0.4,
        (0xFF, 0xFF, 0xFF),
        1,
        cv2.FONT_HERSHEY_COMPLEX_SMALL,
    )


def license_plate_recognition(vehicle):
    vehicle.license_plate = HyperLPR_plate_recognition(vehicle.image)


def draw_total_count(frame, total_passed_vehicle):
    cv2.putText(
        frame,
        'Detected Vehicles: ' + str(total_passed_vehicle),
        (10, 35),
        cv2.FONT_HERSHEY_SIMPLEX
        ,
        0.8,
        (0, 0xFF, 0xFF),
        2,
        cv2.FONT_HERSHEY_SIMPLEX
    )


backend_transport = RequestsHTTPTransport(
    url='http://10.0.0.235:8080/graphql',
    headers={'Authorization': 'token'}
)


def dispatch_vehicle(vehicle: Vehicle):
    with Client(
            transport=backend_transport,
            fetch_schema_from_transport=True,
    ) as session:
        vehicle.image = base64.b64encode(cv2.imencode('.jpg', vehicle.image)[1])

        session.execute(gql('''
            query{
                fuckCrossroad(vehicle: {
                image:"''' + vehicle.image.decode('utf-8') + '''",
                licensePlate: ''' + 'null' + ''',
                color: "''' + vehicle.color + '''",
                speed: ''' + str(vehicle.speed) + '''
            }){
                    licensePlate
                }
            }
            '''))


network_pool = concurrent.futures.ThreadPoolExecutor()


# Detection
async def object_detection_function() -> None:
    prev_vehicle: Union[Vehicle, None] = None
    total_passed_vehicle = 0
    with detection_graph.as_default():
        with tf.Session(graph=detection_graph) as sess:

            # Definite input and output Tensors for detection_graph
            image_tensor = detection_graph.get_tensor_by_name('image_tensor:0')

            # Each box represents a part of the image where a particular object was detected.
            detection_boxes = detection_graph.get_tensor_by_name('detection_boxes:0')

            # Each score represent how level of confidence for each of the objects.
            # Score is shown on the result image, together with the class label.
            detection_scores = detection_graph.get_tensor_by_name('detection_scores:0')
            detection_classes = detection_graph.get_tensor_by_name('detection_classes:0')
            num_detections = detection_graph.get_tensor_by_name('num_detections:0')

            # for all the frames that are extracted from input video
            while cap.isOpened():
                (ret, frame) = cap.read()

                if not ret:
                    print('end of the video file...')
                    break

                input_frame = frame

                # Expand dimensions since the model expects images to have shape: [1, None, None, 3]
                image_np_expanded = np.expand_dims(input_frame, axis=0)

                # Actual detection.
                (boxes, scores, classes, num) = \
                    sess.run([detection_boxes, detection_scores,
                              detection_classes, num_detections],
                             feed_dict={image_tensor: image_np_expanded})

                # Visualization of the results of a detection.
                (counter, csv_line, vehicle) = \
                    vis_util.visualize_boxes_and_labels_on_image_array(
                        cap.get(1),
                        input_frame,
                        np.squeeze(boxes),
                        np.squeeze(classes).astype(np.int32),
                        np.squeeze(scores),
                        category_index,
                        use_normalized_coordinates=True,
                        line_thickness=4, roi_position=200
                    )

                total_passed_vehicle = total_passed_vehicle + counter
                if vehicle is not None:
                    vehicle.image = input_frame[int(vehicle.image["top"] * input_frame.shape[0]):int(
                        vehicle.image["bottom"] * input_frame.shape[0]),
                                    int(vehicle.image["left"] * input_frame.shape[1]):int(
                                        vehicle.image["right"] * input_frame.shape[1])]
                    license_plate_recognition(vehicle)
                    asyncio.get_running_loop().run_in_executor(network_pool, lambda: dispatch_vehicle(vehicle))
                # insert information text to video frame
                draw_total_count(input_frame, total_passed_vehicle)

                draw_roi(counter, input_frame, 200)
                if vehicle is not None:
                    draw_info(input_frame, vehicle)
                    prev_vehicle = vehicle
                else:
                    draw_info(input_frame, prev_vehicle)

                cv2.imshow('vehicle detection', input_frame)

                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

            cap.release()
            cv2.destroyAllWindows()


asyncio.run(object_detection_function())
