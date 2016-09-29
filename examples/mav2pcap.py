#!/usr/bin/env python

# Copyright 2012, Holger Steinhaus
# Released under the GNU GPL version 3 or later

# This program packetizes a binary MAVLink stream. The resulting packets are stored into a PCAP file, which is
# compatible to tools like Wireshark.

# The program tries to synchronize to the packet structure in a robust way, using the SOF magic, the potential
# packet length information and the next SOF magic. Additionally the CRC is verified.

# Hint: A MAVLink protocol dissector (parser) for Wireshark may be generated by mavgen.py.

# dependency: Python construct library (python-construct on Debian/Ubuntu), "easy_install construct" elsewhere


from __future__ import print_function
from future import standard_library
standard_library.install_aliases()
from builtins import chr
from builtins import str
from builtins import object
from builtins import open
import sys
import os

from pymavlink import mavutil

from construct import ULInt16, Struct, Byte, Bytes, Const
from construct.core import FieldError
from argparse import ArgumentParser, FileType


MAVLINK_MAGIC = 0xfe
write_junk = True

# copied from ardupilotmega.h (git changeset 694536afb882068f50da1fc296944087aa207f9f, Dec 02 2012
MAVLINK_MESSAGE_CRCS  = (50, 124, 137, 0, 237, 217, 104, 119, 0, 0, 0, 89, 0, 0, 0, 0, 0, 0, 0, 0, 214, 159, 220, 168, 24, 23, 170, 144, 67, 115, 39, 246, 185, 104, 237, 244, 242, 212, 9, 254, 230, 28, 28, 132, 221, 232, 11, 153, 41, 39, 214, 223, 141, 33, 15, 3, 100, 24, 239, 238, 30, 240, 183, 130, 130, 0, 148, 21, 0, 243, 124, 0, 0, 0, 20, 0, 152, 143, 0, 0, 127, 106, 0, 0, 0, 0, 0, 0, 0, 231, 183, 63, 54, 0, 0, 0, 0, 0, 0, 0, 175, 102, 158, 208, 56, 93, 0, 0, 0, 0, 235, 93, 124, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 42, 241, 15, 134, 219, 208, 188, 84, 22, 19, 21, 134, 0, 78, 68, 189, 127, 111, 21, 21, 144, 1, 234, 73, 181, 22, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 204, 49, 170, 44, 83, 46, 0)


import struct

# Helper class for writing pcap files
class pcap(object):
    """
       Used under the terms of GNU GPL v3.
       Original author: Neale Pickett
       see http://dirtbags.net/py-pcap.html
    """
    _MAGIC = 0xA1B2C3D4
    def __init__(self, stream, mode='rb', snaplen=65535, linktype=1):
        try:
            self.stream = open(stream, mode)
        except TypeError:
            self.stream = stream
        try:
            # Try reading
            hdr = self.stream.read(24)
        except IOError:
            hdr = None

        if hdr:
            # We're in read mode
            self._endian = pcap.None
            for endian in '<>':
                (self.magic,) = struct.unpack(endian + 'I', hdr[:4])
                if self.magic == pcap._MAGIC:
                    self._endian = endian
                    break
            if not self._endian:
                raise IOError('Not a pcap file')
            (self.magic, version_major, version_minor,
             self.thiszone, self.sigfigs,
             self.snaplen, self.linktype) = struct.unpack(self._endian + 'IHHIIII', hdr)
            if (version_major, version_minor) != (2, 4):
                raise IOError('Cannot handle file version %d.%d' % (version_major,
                                                                    version_minor))
        else:
            # We're in write mode
            self._endian = '='
            self.magic = pcap._MAGIC
            version_major = 2
            version_minor = 4
            self.thiszone = 0
            self.sigfigs = 0
            self.snaplen = snaplen
            self.linktype = linktype
            hdr = struct.pack(self._endian + 'IHHIIII',
                              self.magic, version_major, version_minor,
                              self.thiszone, self.sigfigs,
                              self.snaplen, self.linktype)
            self.stream.write(hdr)
        self.version = (version_major, version_minor)

    def read(self):
        hdr = self.stream.read(16)
        if not hdr:
            return
        (tv_sec, tv_usec, caplen, length) = struct.unpack(self._endian + 'IIII', hdr)
        datum = self.stream.read(caplen)
        return ((tv_sec, tv_usec, length), datum)

    def write(self, packet):
        (header, datum) = packet
        (tv_sec, tv_usec, length) = header
        hdr = struct.pack(self._endian + 'IIII', tv_sec, tv_usec, length, len(datum))
        self.stream.write(hdr)
        self.stream.write(datum)

    def __iter__(self):
        while True:
            r = self.read()
            if not r:
                break
            yield r


def find_next_frame(data):
    """
    find a potential start of frame
    """
    return data.find('\xfe')


def parse_header(data):
    """
    split up header information (using construct)
    """
    mavlink_header = Struct('header',
        Const(Byte('magic'), MAVLINK_MAGIC),
        Byte('plength'),
        Byte('sequence'),
        Byte('sysid'),
        Byte('compid'),
        Byte('msgid'),
    )
    return mavlink_header.parse(data[0:6])


def write_packet(number, data, flags, pkt_length):
    pcap_header = (number, flags, pkt_length)
    pcap_file.write((pcap_header, data))


def convert_file(mavlink_file, pcap_file):
    # the whole file is read in a bunch - room for improvement...
    data = mavlink_file.read()

    i=0
    done = False
    skipped_char = None
    junk = ''
    cnt_ok = 0
    cnt_junk = 0
    cnt_crc = 0

    while not done:
        i+=1
        # look for potential start of frame
        next_sof = find_next_frame(data)
        if next_sof > 0:
            print("skipped " + str(next_sof) + " bytes")
            if write_junk:
                if skipped_char != None:
                    junk = skipped_char + data[:next_sof]
                    skipped_char = None
                write_packet(i, junk, 0x03, len(junk))
            data = data[next_sof:]
            data[:6]
            cnt_junk += 1

        # assume, our 0xFE was the start of a packet
        header = parse_header(data)
        payload_len = header['plength']
        pkt_length = 6 + payload_len + 2
        try:
            pkt_crc = ULInt16('crc').parse(data[pkt_length-2:pkt_length])
        except FieldError:
            # ups, no more data
            done = True
            continue

        # peek for the next SOF
        try:
            cc = mavutil.x25crc(data[1:6+payload_len])
            cc.accumulate(chr(MAVLINK_MESSAGE_CRCS[header['msgid']]))
            x25_crc = cc.crc
            if x25_crc != pkt_crc:
                crc_flag = 0x1
            else:
                crc_flag = 0
            next_magic = data[pkt_length]
            if chr(MAVLINK_MAGIC) != next_magic:
                # damn, retry
                print("packet %d has invalid length, crc error: %d" % (i, crc_flag))

                # skip one char to look for a new SOF next round, stow away skipped char
                skipped_char = data[0]
                data = data[1:]
                continue

            # we can consider it a packet now
            pkt = data[:pkt_length]
            write_packet(i, pkt, crc_flag, len(pkt))
            print("packet %d ok, crc error: %d" % (i, crc_flag))
            data = data[pkt_length:]

            if crc_flag:
                cnt_crc += 1
            else:
                cnt_ok += 1


        except IndexError:
            # ups, no more packets
            done = True
    print("converted %d valid packets, %d crc errors, %d junk fragments (total %f%% of junk)" % (cnt_ok, cnt_crc, cnt_junk, 100.*float(cnt_junk+cnt_crc)/(cnt_junk+cnt_ok+cnt_crc)))

###############################################################################

parser = ArgumentParser()
parser.add_argument("input_file", type=FileType('rb'))
parser.add_argument("output_file", type=FileType('wb'))
args = parser.parse_args()


mavlink_file = args.input_file
args.output_file.close()
pcap_file = pcap(args.output_file.name, args.output_file.mode, linktype=147) # special trick: linktype USER0

convert_file(mavlink_file, pcap_file)

mavlink_file.close()
#pcap_file.close()
