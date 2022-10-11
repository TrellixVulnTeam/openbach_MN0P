#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#  OpenBACH is a generic testbed able to control/configure multiple
#  network/physical entities (under test) and collect data from them. It is
#  composed of an Auditorium (HMIs), a Controller, a Collector and multiple
#  Agents (one for each network entity that wants to be tested).
#
#
#  Copyright © 2016-2020 CNES
#
#
#  This file is part of the OpenBACH testbed.
#
#
#  OpenBACH is a free software : you can redistribute it and/or modify it under
#  the terms of the GNU General Public License as published by the Free
#  Software Foundation, either version 3 of the License, or (at your option)
#  any later version.
#
#  This program is distributed in the hope that it will be useful, but WITHOUT
#  ANY WARRANTY, without even the implied warranty of MERCHANTABILITY or
#  FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
#  more details. # # You should have received a copy of the GNU General Public License along with
#  this program. If not, see http://www.gnu.org/licenses/.


"""Sources of the Job d-itg_send"""


__author__ = 'CNES'
__credits__ = '''
Contributor: Guillaume Colombo <guillaume.colombo@cnes.fr>
Matthieu Petrou <matthieu.petrou@viveris.fr>
'''

import sys
import syslog
import pathlib
import argparse
import subprocess
from functools import partial

import collect_agent


def run_command(cmd, wait_finished=True):
    try:
        if wait_finished:
            p = subprocess.run(cmd, stderr=subprocess.PIPE)
        else:
            p = subprocess.Popen(cmd, stderr=subprocess.PIPE)
    except Exception as ex:
        message = 'Error running {} : {}'.format(cmd, ex)
        collect_agent.send_log(syslog.LOG_ERR, message)
        sys.exit(message)

    return p


def memory_size(value, converter=float, multiplier=1024):
    import re
    match = re.fullmatch(r'(\d+\.?\d*)(K|M|G)?', value)
    if not match:
        message = 'wrong format: use {} followed by an optionnal K, M, or G'.format(converter.__name__)
        raise argparse.ArgumentError(message)

    base, unit = match.groups()
    if unit == 'K':
        return converter(base) * multiplier
    elif unit == 'M':
        return converter(base) * multiplier * multiplier
    elif unit == 'G':
        return converter(base) * multiplier * multiplier * multiplier
    else:
        return converter(base)


def main(target_address, log_address, dest_path, granularity, traffic_type='UDP', port=8999, signal_port=9000,
         packet_size=512, packet_rate=1000, bandwidth=0, duration=10, data_size=0, meter='owdm'):

    # Clean previous log and set up the D-ITG LogServer*
    pathlib.Path('/tmp/ITGRecv.log').unlink(missing_ok=True)
    pathlib.Path('/tmp/ITGSend.log').unlink(missing_ok=True)

    proc_log = run_command('ITGLog', False)

    # Get the reference time for changing the stats generated by D-ITG
    time_ref = collect_agent.now()

    #Set packet_rate depending on bandwidth parameter
    if bandwidth:
        packet_rate = bandwidth / (8 * packet_size)

    # Build and launch the D-ITGSend command
    cmd_send = [
            'ITGSend', '-a', target_address, '-L', log_address,
            '-X', log_address, '-T', traffic_type, '-c', str(packet_size),
            '-C', str(packet_rate), '-t', str(duration*1000), '-m', meter,
            '-Sdp', str(signal_port), '-Ssp', str(signal_port), '-rp', str(port),
    ]
   
    #Set number of KBytes to generate
    if data_size:
        cmd_send += ['-k', str(data_size)]

    run_command(cmd_send)

    # Terminate the process of the D-ITG LogServer
    proc_log.terminate()

    rcv_path = pathlib.Path(dest_path, 'RCV')
    snd_path = pathlib.Path(dest_path, 'SND')

    # Clear potential old stats
    rcv_path.unlink(missing_ok=True)
    snd_path.unlink(missing_ok=True)

    # Get the stats from the logs
    run_command(['ITGDec', '/tmp/ITGRecv.log', '-c', str(granularity), rcv_path.as_posix()])
    run_command(['ITGDec', '/tmp/ITGSend.log', '-c', str(granularity), snd_path.as_posix()])

    # Send the stats of the receiver to the collector
    try:
        stats = rcv_path.open('r')
    except Exception as ex:
        message = 'Error opening file {} : {}'.format(rcv_path, ex)
        collect_agent.send_log(syslog.LOG_ERR, message)
        sys.exit(message)
    
    owd_r = []
    with stats :
        for line in stats:
            txt = line.strip()
            txt = txt.split(' ')

            # Get the timestamp (in ms)
            timestamp = txt[0].replace('.','')
            timestamp = int(timestamp[:-3])
            timestamp = timestamp + time_ref

            # Get the bitrate (in bps)
            bitrate = txt[1]
            bitrate = float(bitrate)*1024

            # Get the delay (in ms)
            delay = txt[2]
            delay = float(delay)*1000
            owd_r.append(delay)

            # Get the jitter (in ms)
            jitter = txt[3]
            jitter = float(jitter)*1000

            # Get the packetloss
            pck_loss = txt[4]
            pck_loss = float(pck_loss)

            # Calculate packet_loss_rate
            pck_loss_per_sec = pck_loss*1000/granularity
            plr = (pck_loss_per_sec/packet_rate)*100

            collect_agent.send_stat(
                    timestamp,
                    bitrate_receiver=bitrate,
                    owd_receiver=delay,
                    jitter_receiver=jitter,
                    packetloss_receiver=pck_loss,
                    packetloss_rate_receiver=plr,
            )

    # Send the stats of the sender to the collector
    try:
        snd_path.open('r')
    except Exception as ex:
        message = 'Error opening file {} : {}'.format(snd_path, ex)
        collect_agent.send_log(syslog.LOG_ERR, message)
        sys.exit(message)

    owd_s = []
    timetab = []
    with stats:
        for line in stats:
            txt = line.strip()
            txt = txt.split(' ')
    
            # Get the timestamp (in ms)
            timestamp = txt[0].replace('.','')
            timestamp = int(timestamp[:-3])
            timestamp = timestamp + time_ref
    
            # Get the bitrate (in bps)
            bitrate = txt[1]
            bitrate = float(bitrate)*1024
    
            if meter.upper() == "RTTM":
                # Get the delay (in ms)
                delay = txt[2]
                delay = float(delay)*1000
                owd_s.append(delay)
                timetab.append(timestamp)
    
                # Get the jitter (in ms)
                jitter = txt[3]
                jitter = float(jitter)*1000
    
                # Get the packetloss
                pck_loss = txt[4]
                pck_loss = float(pck_loss)

                # Calculate packet_loss_rate
                pck_loss_per_sec = pck_loss*1000/granularity
                plr = (pck_loss_per_sec/packet_rate)*100

                collect_agent.send_stat(
                        timestamp,
                        bitrate_sender=bitrate,
                        rtt_sender=delay,
                        jitter_sender=jitter,
                        packetloss_sender=pck_loss,
                        packetloss_rate_sender=plr,
                )
            else:
                collect_agent.send_stat(timestamp, bitrate_sender=bitrate)

    if meter.upper() == 'RTTM':
        for time_tab, owdr, owds in zip(timetab, owd_r, owd_s):
          owd_return = owds - owdr
          statistics = {'owd_return': owd_return}
          collect_agent.send_stat(time_tab, **statistics)


if __name__ == "__main__":
    with collect_agent.use_configuration("/opt/openbach/agent/jobs/d-itg_send/d-itg_send_rstats_filter.conf"):
        # Define Usage
        parser = argparse.ArgumentParser(
                description='Create a D-ITG command',
                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument(
                'target_address',
                type=str, metavar='Target_address',
                help='Address IP where the flow is sent')
        parser.add_argument(
                'sender_address',
                type=str, metavar='sender_address',
                help="Address of the sender to get the receiver's logs")
        parser.add_argument(
                'dest_path',
                type=str, metavar='Destination_path',
                help='Path where the stats will be located')
        parser.add_argument(
                'granularity',
                type=int, metavar='Granularity',
                help='Set the granularity (in ms) at which the stats will be generated')
        parser.add_argument(
                '-T', '--traffic_type',
                type=str, metavar='TRAFFIC TYPE', default='UDP',
                help='Traffic type (UDP, TCP, ICMP, ...) (default=UDP)')
        parser.add_argument(
                '-p', '--port',
                type=int, metavar='PORT', default=8999,
                help='Set server port (default=8999)')
        parser.add_argument(
                '-P', '--signal_port',
                type=int, metavar='SIGNAL PORT', default=9000,
                help='Set port for signal transmission (default=9000)')
        parser.add_argument(
                '-c', '--packet_size',
                type=int, metavar='PACKET SIZE', default=512,
                help='Size of each packet in byte (default=512)')
        parser.add_argument(
                '-C', '--packet_rate',
                type=int, metavar='PACKET RATE', default=1000,
                help='Number of packets to send in one second (default=1000)')
        parser.add_argument(
                '-B', '--bandwidth',
                type=partial(memory_size, converter=int, multiplier=1000), metavar='BANDWIDTH', default=0,
                help='Set bandwidth in [K/M/G]bits/s, if set will overrun packet_rate (default=0)')
        parser.add_argument(
                '-d', '--duration',
                type=int, metavar='DURATION', default=10,
                help='Duration of the traffic in s (default=10)')
        parser.add_argument(
                '-k', '--data_size',
                type=memory_size, metavar='DATA SIZE', default=0,
                help='Set the number of [K/M/G]Bytes to send, if set either duration or data_size will limit the job')
        parser.add_argument(
                '-m', '--meter',
                type=str, metavar='METER',
                choices=['owdm', 'rttm'], default='owdm',
                help='Way to compute the time: One Way Delay (owdm) or Round Trip Time (rttm) (default=owdm)')

        # get args
        args = parser.parse_args()
        target_address = args.target_address
        sender_address = args.sender_address
        dest_path = args.dest_path
        granularity = args.granularity
        traffic_type = args.traffic_type
        port = args.port
        signal_port = args.signal_port
        packet_size = args.packet_size
        packet_rate = args.packet_rate
        bandwidth = args.bandwidth
        duration = args.duration
        data_size = args.data_size
        meter = args.meter
   
        main(target_address, sender_address, dest_path, granularity, traffic_type, port, signal_port, packet_size, packet_rate, bandwidth, duration, data_size, meter)
