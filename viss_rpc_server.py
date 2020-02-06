#!/usr/bin/env python3

# (C) 2020 Jaguar Land Rover.
#
# This program is licensed under the terms and conditions of the
# Mozilla Public License, version 2.0.  The full text of the
# Mozilla Public License is at https://www.mozilla.org/MPL/2.0/
#
# Author: Magnus Feuer (mfeuer1@jaguarlandrover.com)
#

import asyncio
import websockets
import json
import yaml
import sys
import time
import random

func_map = {}
subs_map = {}
subscription_id = 1

signals = [ { 'path': "Vehicle.Drivetrain.InternalCombustionEngine.Engine.Speed",
              'type': "uint16",
              'min':  0,
              'max':  20000} ,

            { 'path': "Vehicle.DriveTrain.FuelSystem.Level",
              'type': "uint8",
              'min':  0,
              'max':  100 },

            { 'path': "Vehicle.DriveTrain.FuelSystem.Range",
              'type': "uint32",
              'min':  0,
              'max':  300000000 },

            { 'path': "Vehicle.DriveTrain.Transmission.Gear",
              'type': "int8",
              'min':  -1,
              'max':  8 } ]

def msec_utc():
    return int(round(time.time() * 1000))

def die(msg):
    print(msg)
    sys.exit(255)

async def reply(websocket, action, request_id, extra_elem = {}, number=0, reason='', message = ''):
    reply_obj = {
        'action': action,
        'requestId': request_id,
        'timestamp': msec_utc()
    }

    print(f"Extra elem {extra_elem}")
    if len(extra_elem) > 0:
        reply_obj.update(extra_elem)

    if number != 0:
        reply_obj['error'] = {
            'number': number,
            'reason': reason,
            'message': message
        }

    print("SENDING REPLY TO TABLET:")
    print(json.dumps(reply_obj, indent=2, sort_keys = False))
    print("-----\n")
    await websocket.send(json.dumps(reply_obj))

def map_type_to_struct_char(arg_type, arg_size):
    type_map = {
        "char": "b",
        "int8_t": "b",
        "uint8_t": "B",
        "int16_t": "h",
        "uint16_t": "H",
        "int32_t": "i",
        "uint32_t": "U",
        "bool": "B",
        "float": "f",
        "double": "d",
        "dynamic": "#",
        "string": "#",
    }

    if arg_size > 1:
        return f"{arg_size}{type_map[arg_type]}"
    else:
        return type_map[arg_type]


def create_struct_signature(signature):
    fmt_string=""
    for arg in signature.items():
        fmt_string += map_type_to_struct_char(arg['type'], arg['size'])
    return fmt_string

def convert_arg(value, val_type):
    if value == None:
        value = ""

    if isinstance(value, list):
        res = []
        for v in value:
            res.append(convert_arg(v, val_type))
        return res

    if val_type in ["int8", "uint8", "int16", "uint16", "int32", "uint32", "bool" ]:
        if value == "":
            return 0

        return int(value)

    if val_type in ["float", "double"]:
        if value == "":
            return 0.0

        return float(value)

    if val_type == "string":
        return value

    die(f"Unknown type: {val_type}")



def process_signal(path, sig_type, value):
    print(f"Proc signal {path} = {value}")
    # Signal type is picked from
    loop = asyncio.get_event_loop()
    value = convert_arg(value, sig_type)
    # Locate all subscribers

    # Iterate over all subscribers and send an update to them
    if path not in subs_map:
        # print(f"Received a signal {path}:{sig_type}={value}/{type(value)} - No subscribers")
        return True

    print(f"Received a signal {path}:{sig_type}={value}/{type(value)} - Publishing over VISS")
    for sub in subs_map[path]['subscribers']:
        send_obj = {
            'action': "subscription",
            'subscriptionId': sub['subscription_id'],
#            'signal': path,
#            'type': sig_type,
            'timestamp': msec_utc(),
            'value': value
        }
        print("SENDING SIGNAL TO TABLET:")
        print(json.dumps(send_obj, indent=2, sort_keys = False))
        print("-----\n")
        #loop.call_soon(sub['socket'].send,json.dumps(send_obj))
        asyncio.ensure_future(sub['socket'].send(json.dumps(send_obj)))

async def process_ws_subscribe(websocket, request_id, json_obj):
    global subs_map
    global subscription_id

    print(f"Subscribe signal: {json_obj}")

    if "path" not in json_obj:
        await reply(websocket, 'subscribe', request_id, {},
                    400, "missing_argument",
                    f"Missing string argument 'path' in {json.dumps(json_obj,indent=2)}")
        return False

    path = json_obj['path']
    s_id = subscription_id
    subscription_id = subscription_id + 1

    if not path in subs_map:
        print(f"Subscribing to {path}")
        subs_map[path] = {
            'subscribers': [ { 'socket': websocket, 'subscription_id': s_id} ]
        }


    # We already have a signal subsdcription to a websocket.
    subs = subs_map[path]

    # Only add the websocket to active subscribers if
    # it is not already in there
    if not websocket in subs['subscribers']:
        subs['subscribers'].append({ 'socket': websocket, 'subscription_id': s_id})

    await reply(websocket, 'subscribe', request_id,
                { 'requestId': request_id, 'subscriptionId': s_id })
    return True


async def process_ws_call(websocket, request_id, json_obj):
    global func_map

    if "function" not in json_obj:
        await reply(websocket, 'reply', request_id, {},
                    "missing_argument",
                    f"Missing string argument 'function' in {json.dumps(json_obj,indent=2)}")
        return False

    func_name = json_obj['function']

    if "arguments" not in json_obj:
        await reply(websocket, 'reply', request_id, {},
                    400, "missing_argument",
                    f"Missing list argument 'argument' in {json.dumps(json_obj,indent=2)}")
        return False

    args = json_obj['arguments']
    arg_tuple = ()

    for arg in args:
        if not "type" in arg:
            await reply(websocket, 'reply', request_id, {},
                        400, "missing_argument",
                        f"Missing string 'type' in {json.dumps(json_obj,indent=2)}")
            return False

        if not "size" in arg:
            await reply(websocket, 'reply', request_id, {},
                        400, "missing_argument",
                        f"Missing string 'size' in {json.dumps(json_obj,indent=2)}")

            return False

        if not "value" in arg:
            await reply(websocket, 'reply', request_id, {},
                        400, "missing_argument",
                        f"Missing string 'value' in {json.dumps(json_obj,indent=2)}")
            return False

        if arg['type'] not in ["int8", "uint8", "int16", "uint16", "int32", "uint32",
                               "bool", "float", "double", "string" ]:
            await reply(websocket, 'call', request_id, {},
                        400, "unknown_type",
                        (f"Unknown argument type {arg['type']}\n"
                         "'type' needs to be one of\n"
                         "  int8, uint8, int16, uint16, int32, uint32\n"
                         "  bool, float, double, string"))
            return False


        # Add naked value to argument array if length is 1.
        elif len(arg["value"]) == 1:
            arg_tuple = arg_tuple + (convert_arg(arg['value'][0], arg['type']),)
        else:
            arg_tuple = arg_tuple + (convert_arg(arg["value"], arg['type']),)



    print(f"Function: {func_name}")
    print(f"Arg: {arg_tuple}")

    await reply(websocket, 'reply', request_id, { 'reply': [{ 'type': "int", 'size': 1, 'value': 4711 }] })
    return True

async def process_ws_request(websocket, path):
    try:
        while True:
            raw_json = await websocket.recv()
            json_obj = json.loads(raw_json)
            print("RECEIVED FROM CLIENT:")
            print(json.dumps(json_obj, indent=2, sort_keys = False))
            print("-----\n")

            if not 'requestId' in json_obj:
                await reply(websocket, 0, {},
                            400, "missing_argument",
                            f"Missing string 'requestId' in {json.dumps(json_obj,indent=2)}")
                continue
            request_id = json_obj['requestId']

            if not 'action' in json_obj:
                await reply(websocket, request_id,
                            400, "missing_argument",
                            f"Missing string 'action' in {json.dumps(json_obj,indent=2)}")
                continue

            cmd = json_obj['action']

            if cmd == 'call':
                await process_ws_call(websocket, request_id, json_obj)
            elif cmd == 'subscribe':
                await process_ws_subscribe(websocket, request_id, json_obj)
            else:
                await reply(websocket, request_id,
                            503, "unknown_action", f"Unknown action: {cmd}")
                continue

    except websockets.exceptions.ConnectionClosed as e:
        print("Connection closed")

    return True

async def publish_signals():
    while True:
        await asyncio.sleep(random.uniform(0.1, 5.0))
        sig = random.choice(signals)
        sig_val = random.randint(sig['min'], sig['max'])
        process_signal(sig['path'], sig['type'], str(sig_val))

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    print("Please connect tablet to port 8088")

    start_server = websockets.serve(process_ws_request,
                                    "0.0.0.0",
                                    8088)

    asyncio.ensure_future(start_server)
    asyncio.ensure_future(publish_signals())
    while True:
        try:
            loop.run_forever()
        except:
            sys.exit(0)
