#!/usr/bin/python
#
# Python class for controlling wpa_supplicant
# Copyright (c) 2013, Jouni Malinen <j@w1.fi>
#
# This software may be distributed under the terms of the BSD license.
# See README for more details.

import os
import time
import logging
import re
import wpaspy

logger = logging.getLogger(__name__)
wpas_ctrl = '/var/run/wpa_supplicant'

class WpaSupplicant:
    def __init__(self, ifname):
        self.ifname = ifname
        self.ctrl = wpaspy.Ctrl(os.path.join(wpas_ctrl, ifname))
        self.mon = wpaspy.Ctrl(os.path.join(wpas_ctrl, ifname))
        self.mon.attach()

    def request(self, cmd):
        logger.debug(self.ifname + ": CTRL: " + cmd)
        return self.ctrl.request(cmd)

    def ping(self):
        return "PONG" in self.request("PING")

    def reset(self):
        self.request("P2P_STOP_FIND")
        self.request("P2P_FLUSH")
        self.request("P2P_GROUP_REMOVE *")
        self.request("REMOVE_NETWORK *")
        self.request("REMOVE_CRED *")

    def get_status(self, field):
        res = self.request("STATUS")
        lines = res.splitlines()
        for l in lines:
            [name,value] = l.split('=', 1)
            if name == field:
                return value
        return None

    def p2p_dev_addr(self):
        return self.get_status("p2p_device_address")

    def p2p_listen(self):
        return self.request("P2P_LISTEN")

    def p2p_find(self, social=False):
        if social:
            return self.request("P2P_FIND type=social")
        return self.request("P2P_FIND")

    def wps_read_pin(self):
        #TODO: make this random
        self.pin = "12345670"
        return self.pin

    def peer_known(self, peer, full=True):
        res = self.request("P2P_PEER " + peer)
        if peer.lower() not in res.lower():
            return False
        if not full:
            return True
        return "[PROBE_REQ_ONLY]" not in res

    def discover_peer(self, peer, full=True, timeout=15):
        logger.info(self.ifname + ": Trying to discover peer " + peer)
        if self.peer_known(peer, full):
            return True
        self.p2p_find()
        count = 0
        while count < timeout:
            time.sleep(1)
            count = count + 1
            if self.peer_known(peer, full):
                return True
        return False

    def group_form_result(self, ev, expect_failure=False):
        if expect_failure:
            if "P2P-GROUP-STARTED" in ev:
                raise Exception("Group formation succeeded when expecting failure")
            exp = r'<.>(P2P-GO-NEG-FAILURE) status=([0-9]*)'
            s = re.split(exp, ev)
            if len(s) < 3:
                return None
            res = {}
            res['result'] = 'go-neg-failed'
            res['status'] = int(s[2])
            return res

        if "P2P-GROUP-STARTED" not in ev:
            raise Exception("No P2P-GROUP-STARTED event seen")

        exp = r'<.>(P2P-GROUP-STARTED) ([^ ]*) ([^ ]*) ssid="(.*)" freq=([0-9]*) ((?:psk=.*)|(?:passphrase=".*")) go_dev_addr=([0-9a-f:]*)'
        s = re.split(exp, ev)
        if len(s) < 8:
            raise Exception("Could not parse P2P-GROUP-STARTED")
        res = {}
        res['result'] = 'success'
        res['ifname'] = s[2]
        res['role'] = s[3]
        res['ssid'] = s[4]
        res['freq'] = s[5]
        p = re.match(r'psk=([0-9a-f]*)', s[6])
        if p:
            res['psk'] = p.group(1)
        p = re.match(r'passphrase="(.*)"', s[6])
        if p:
            res['passphrase'] = p.group(1)
        res['go_dev_addr'] = s[7]
        return res

    def p2p_go_neg_auth(self, peer, pin, method, go_intent=None):
        if not self.discover_peer(peer):
            raise Exception("Peer " + peer + " not found")
        self.dump_monitor()
        cmd = "P2P_CONNECT " + peer + " " + pin + " " + method + " auth"
        if go_intent:
            cmd = cmd + ' go_intent=' + str(go_intent)
        if "OK" in self.request(cmd):
            return None
        raise Exception("P2P_CONNECT (auth) failed")

    def p2p_go_neg_auth_result(self, timeout=1, expect_failure=False):
        ev = self.wait_event(["P2P-GROUP-STARTED","P2P-GO-NEG-FAILURE"], timeout);
        if ev is None:
            if expect_failure:
                return None
            raise Exception("Group formation timed out")
        self.dump_monitor()
        return self.group_form_result(ev, expect_failure)

    def p2p_go_neg_init(self, peer, pin, method, timeout=0, go_intent=None, expect_failure=False):
        if not self.discover_peer(peer):
            raise Exception("Peer " + peer + " not found")
        self.dump_monitor()
        cmd = "P2P_CONNECT " + peer + " " + pin + " " + method
        if go_intent:
            cmd = cmd + ' go_intent=' + str(go_intent)
        if "OK" in self.request(cmd):
            if timeout == 0:
                self.dump_monitor()
                return None
            ev = self.wait_event(["P2P-GROUP-STARTED","P2P-GO-NEG-FAILURE"], timeout)
            if ev is None:
                if expect_failure:
                    return None
                raise Exception("Group formation timed out")
            self.dump_monitor()
            return self.group_form_result(ev, expect_failure)
        raise Exception("P2P_CONNECT failed")

    def wait_event(self, events, timeout):
        count = 0
        while count < timeout * 2:
            count = count + 1
            time.sleep(0.1)
            while self.mon.pending():
                ev = self.mon.recv()
                logger.debug(self.ifname + ": " + ev)
                for event in events:
                    if event in ev:
                        return ev
        return None

    def dump_monitor(self):
        while self.mon.pending():
            ev = self.mon.recv()
            logger.debug(self.ifname + ": " + ev)

    def remove_group(self, ifname=None):
        if ifname is None:
            ifname = self.ifname
        if "OK" not in self.request("P2P_GROUP_REMOVE " + ifname):
            raise Exception("Group could not be removed")

    def p2p_start_go(self):
        self.dump_monitor()
        cmd = "P2P_GROUP_ADD"
        if "OK" in self.request(cmd):
            ev = self.wait_event(["P2P-GROUP-STARTED"], timeout=5)
            if ev is None:
                raise Exception("GO start up timed out")
            self.dump_monitor()
            return self.group_form_result(ev)
        raise Exception("P2P_GROUP_ADD failed")

    def p2p_go_authorize_client(self, pin):
        cmd = "WPS_PIN any " + pin
        if "FAIL" in self.request(cmd):
            raise Exception("Failed to authorize client connection on GO")
        return None

    def p2p_connect_group(self, go_addr, pin, timeout=0):
        self.dump_monitor()
        if not self.discover_peer(go_addr):
            raise Exception("GO " + go_addr + " not found")
        self.dump_monitor()
        cmd = "P2P_CONNECT " + go_addr + " " + pin + " join"
        if "OK" in self.request(cmd):
            if timeout == 0:
                self.dump_monitor()
                return None
            ev = self.wait_event(["P2P-GROUP-STARTED"], timeout)
            if ev is None:
                raise Exception("Joining the group timed out")
            self.dump_monitor()
            return self.group_form_result(ev)
        raise Exception("P2P_CONNECT(join) failed")
