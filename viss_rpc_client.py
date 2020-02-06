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
import sys
import getopt

pending_subscription_req = {}
subscriptions = {}

def die(msg):
    print(msg)
    sys.exit(255)

def usage(name):
    print(f"Usage {name} ws://<host>:<port> [-s signal ...] [<function>[argument ...]]")
    print("signal:     Signal(s) to subscribe to.")
    print("function:   DSTC function")
    print("argument:   Function argument. Format is:")
    print("            <type>:<value> Single element - int32:4711")
    print("            <type>:<len>:<value> Array element - int8:32:Hello world")
    print("            <type> can be: int8, uint8, int16, uint16, int32, uint32")
    print("                           bool, float, double, string")
    print("            Variable-length string argument type has the format: string:<string>")
    print(f"Example: {sys.argv[0]} ws://localhost:4711 print_name_and_age string:32 'Bob Smith' int32:42")


def get_next_request_id():
    get_next_request_id.request_id = get_next_request_id.request_id + 1
    return str(get_next_request_id.request_id)

get_next_request_id.request_id = 0

def process_subscribe_reply(json_obj):
    global subscriptions

    # Caller has verified that requestId is in json_obj
    request_id = json_obj['requestId']

    if not request_id in pending_subscription_req:
        print("Error: Got a subscription reply for request ID {request_id} that I never used in a subscription.")

    # Retrieve signal associated with request id that we sent with
    # subscription request.
    signal = pending_subscription_req[request_id]
    pending_subscription_req.pop(request_id)

    if not 'subscriptionId' in json_obj:
        print("Error: Missing 'subscriptionId' in subscription notification sent from server")
        subscription_id = 'MISSING'
    else:
        subscription_id = json_obj['subscriptionId']

    subscriptions[subscription_id] = signal

    print(f"Received subscription reply RequestID({request_id}) -> SubscriptionID({subscription_id}) -> {signal}")


def display_subscription(json_obj):
    global subscriptions

    if not 'subscriptionId' in json_obj:
        print("Error: Missing 'subscriptionId' in subscription notification sent from server")
    else:
        subscriptionId = 'MISSING'

    # Retrieve the signal path from our subscriptions dictionary.
    if not json_obj['subscriptionId'] in subscriptions:
        print(f"Error: received subscriptionId that we dont recognize")
        subscriptionId = 'MISSING'
    else:
        subscriptionId = subscriptions[json_obj['subscriptionId']]

    if not "value" in json_obj:
        print("Error: Missing 'value' in subscription notification sent from server")
        value = 'MISSING'
    else:
        value = json_obj['value']

    if not "timestamp" in json_obj:
        print("Note: Missing 'timestamp' in subscription notification sent from server")
        timestamp = 'MISSING'
    else:
        timestamp = json_obj['timestamp']

    print(f"Received subscription: subscriptionId: -> {subscriptionId} -> {value} -> {timestamp}")


def display_error_response(json_obj):
    print(f"Received error reply for transaction {json_obj['requestId']}: {json_obj['error']['number']}: {json_obj['error']['reason']}: {json_obj['error']['message']}")
    return False


async def process_websocket(ws):

    raw_json = await ws.recv()
    json_obj = json.loads(raw_json)

    print("RECEIVED FROM server:")
    print(raw_json)
    print("-----\n")
    if not 'action' in json_obj:
        print("Error: Missing 'action' in traffic from server")

    if json_obj['action'] == 'subscribe':
        print('subscribe means server is responding to a subscribe request')
        process_subscribe_reply(json_obj)
    elif json_obj['action'] == 'subscription':
        print('Received subscription notification from server publishing a signal')
        display_subscription(json_obj)
    else:
        print('Error: received unknown action')


async def subscribe_to_signal(ws, signal):
    global pending_subscription_req
    req_id = get_next_request_id()
    sub_cmd = {
        "action": "subscribe",
        "requestId": req_id,
        "path": signal
    }

    pending_subscription_req[req_id] = signal

    #print("Sending Subscribe: {}\n".format(json.dumps(sub_cmd, indent=2)))
    await ws.send(json.dumps(sub_cmd))


async def process_rpc_call(ws, cmd_array):
    func_name = cmd_array[0]
    args = cmd_array[1:]

    arg_arr = []
    for arg in args:
        first_colon = arg.find(':')
        if first_colon == -1:
            print(f"Argument {arg} lacks colon")
            return None

        arg_type = arg[:first_colon]
        if arg_type not in ["int8", "uint8", "int16", "uint16", "int32", "uint32",
                            "bool", "float", "double", "string", "callback" ]:
            print(f"Argument {arg} has an unknown type: {arg_type}")
            print("Please use one of:")
            print("  int8, uint8, int16, uint16, int32, uint32")
            print("  bool, float, double, string")
            return None

        arg_val = arg[first_colon + 1:]

        # Do we have a second colon?
        # int32:4:1,2,3,4

        second_colon = arg_val.find(':')
        arg_sz = 1
        if second_colon != -1:
            arg_sz = arg_val[:second_colon]
            arg_val = arg_val[second_colon + 1:]

            if arg_type != "string":
                arg_val = arg_val.split(",")
                if len(arg_val) != arg_sz:
                    print(f"{arg}: {arg_sz} arguments specified, but {arg_val} arguments given.")
                    return None

        # Add naked value to argument array
        arg_arr.append({
            "type": arg_type,
            "size": arg_sz,
            "value": arg_val
        })

    json_cmd = {
        "action": "call",   # This is a remote procedure call
        "requestId": get_next_request_id(),  # Arbitrary transaction ID to be sent back with reply
        "function": func_name,
        "arguments": arg_arr
    }

    print("Sending Command: {}\n".format(json.dumps(json_cmd, indent=2)))
    await ws.send(json.dumps(json_cmd))


async def main_loop(signal_sub, args):
    async with websockets.connect(args[0]) as ws:
        for signal in signal_sub:
            await subscribe_to_signal(ws, signal)

        # Send a command, if we have any.
        if len(args) > 1:
            await process_rpc_call(ws, args[1:])

        # Process the reply from the command sent above
        # and signals. Abort with Ctrl-c
        while True:
            await process_websocket(ws)

if __name__ == "__main__":
    signal_sub = []
    server = None
    call = ""
    try:
        opts, args = getopt.getopt(sys.argv[1:], "S:s:c:", ["subscribe=", "server=", "call="])
    except getopt.GetoptError as err:
        print(f"Error parsing arguments: {err}")
        usage(sys.argv[0])
        sys.exit(255)

    for o, a in opts:
        if o == "-s":
            print("Subcribing to signal {}".format(a))
            signal_sub.append(a)
        elif o == "-S":
            print("Server {}".format(a))
            server = a
        elif o == "-c":
            print("Call {}".format(a))
            call = a
        else:
            assert False, "unhandled option"


    if not server:
        print("\nNo -S server specified.\n")
        usage(sys.argv[0])
        sys.exit(255)

    if not call:
        print("""\nNo call using -c 'function [arg] [...]' specified.\n""")
        usage(sys.argv[0])
        sys.exit(255)


    if not server:
        usage(sys.argv[0])
        sys.exit(255)

    print(f"Arguments: {args}")
    asyncio.get_event_loop().run_until_complete(main_loop(server, call, signal_sub))
