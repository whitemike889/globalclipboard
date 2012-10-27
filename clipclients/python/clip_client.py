#!/usr/bin/env python

import json
import pyperclip
import os
import errno
import logging
import requests
from threading import Timer
from Crypto.Cipher import DES3
from Crypto import Random

try:
    from cStringIO import StringIO
except:
    from StringIO import StringIO


VERSION = '1.0'
CONF_FILE='~/.global_clip/client.conf'



#----------- helpers --------------------
def encrypt_file(in_s, out_s, chunk_size, key, iv):
    des3 = DES3.new(key, DES3.MODE_CFB, iv)
    while True:
        chunk = in_s.read(chunk_size)
        if len(chunk) == 0:
            break
        elif len(chunk) % 16 != 0:
            chunk += ' ' * (16 - len(chunk) % 16)
        out_s.write(des3.encrypt(chunk))

def decrypt_file(in_s, out_s, chunk_size, key, iv):
    des3 = DES3.new(key, DES3.MODE_CFB, iv)
    while True:
        chunk = in_s.read(chunk_size)
        if len(chunk) == 0:
            break
        out_s.write(des3.decrypt(chunk))


def make_sure_path_exists(path):
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

def save_conf(conf):
    if conf:
        try:
            with open(os.path.expanduser(CONF_FILE),'w') as f:
                f.write(json.dumps(conf, sort_keys=True, indent=4))
        except:
            logging.error("Failed to Save Configuration file")

def save_session(conf,session_key):
    if session_key:
        try:
            with open(os.path.expanduser(conf['sessions']['session_file']),'w') as f:
                f.write(session_key)
            logging.info("Saved Session Key")
        except:
            logging.error("Failed to write Session file")

def get_session(conf):
    if conf is None:
        return None
    else:
        key = None
        try:
            with open(os.path.expanduser(conf['sessions']['session_file']),'r') as f:
                key = f.readline()
        except:
            logging.error("Failed to read Session File")
        return key

def credentials(conf):
    return conf['account']

def get_encryptionconf(conf):
    econf = {
        'key' : None,
    }

    if 'crypto' in conf:
        econf.update(conf['crypto'])

    #set a random initial-vector to be used during this session...
    econf.update(
            {
                'initial_vector' : Random.get_random_bytes(8)
            })

    return econf

def encrypt_clip(text,econf):
    if econf['key'] is not None:
        initial_vector = econf['initial_vector']
        in_s = StringIO(text)
        out_s = StringIO()
        key = econf['key']
        BLOCK_SIZE = 8192
        encrypt_file(in_s,out_s,BLOCK_SIZE,key,initial_vector)
        return out_s.getvalue()
    else:
        raise "No Encryption Key Configured Yet!"

def decrypt_clip(text,econf):
    if econf['key'] is not None:
        initial_vector = econf['initial_vector']
        in_s = StringIO(text)
        out_s = StringIO()
        key = econf['key']
        BLOCK_SIZE = 8192
        decrypt_file(in_s,out_s,BLOCK_SIZE,key,initial_vector)
        return out_s.getvalue()
    else:
        raise "No Encryption Key Configured Yet!"


#-------------- End Helpers ----------------

make_sure_path_exists(os.path.dirname(os.path.expanduser(CONF_FILE)))

#configuration defaults...in case user doesn't set em...
conf = {
    "server" : {
        "url" : "http://localhost/clip"
    },

    "account" : {
        "username" : "joewillrich@gmail.com",
        "password" : "password"
    },

    "clipboard" : {
        "intervals" : {
            "copy" : 10,
            "paste" : 10
        }
    },

    "sessions" : {
        "session_file" : "~/.global_clip/session",
        "allow_reset" : "true"
    },

    "logging" : {
        "log_file" : "/tmp/global_clip.log"
    },
    "crypto" : {
        "key" : "REPLACE-ME-PLEASE!"
    }
}

user_conf = None
try:
    with open(os.path.expanduser(CONF_FILE),'r') as f:
        user_conf = json.loads(''.join(f.readlines()))
        if type(user_conf) is not dict:
            raise "Invalid Conf!"
except:
    print "Failed to Read Conf File"

conf.update(user_conf if user_conf is not None else {})

#initialize logs
print "Starting with Log : %s " % conf['logging']['log_file']

logging.basicConfig(level=logging.INFO, filename=conf['logging']['log_file'])

logging.info("Starting Global Clipboard Client [version %s]" % VERSION)

if user_conf is None:
    logging.warn("No valid User Configuration found, proceeding with defaults")
    save_conf(conf)
else:
    save_conf(conf)#just incase user never defined some defaults...

local_clipboard = None
session_key = get_session(conf)
creds = credentials(conf)

encryption_conf = get_encryptionconf(conf)

#periodically check for new items on the local clipboard and paste them onto the global clip
#similarly, check for new items on the global clipboard and copy them onto the local clip
def run_clipclient():
    global local_clipboard
    global session_key
    global creds
    t = None
    if  session_key is  None:
        #try to get a session...
        r = requests.request('post',"%s/session" % conf['server']['url'], data = {'email' : creds['username'] , 'password' : creds['password'] })
        rj = r.json
        if r.ok and rj['status'] == 200:
            session_key = rj['payload']
            save_session(conf,session_key)
            logging.info('Obtained New Session Key')
        else:
            logging.error('Failed to obtain New Session Key [%s]' % rj['payload'])

    if session_key is None:
        #try to login then...
        r = requests.request('post',"%s/register" % conf['server']['url'], data = {'email' : creds['username'] , 'password' : creds['password'] })
        rj = r.json
        if r.ok and rj['status'] == 200:
            logging.info("Registration Successfull: %s" % rj['payload'])
            #since's we've just registered... give user 1 min to complete registration before using clip client again
            t = Timer(60,run_clipclient) 
        else:
            logging.error("Registration Not Completed : %s" % rj['payload'])
            #possibly, registration not complete yet or activation not yet done. delay for like 2 min now before trying again
            t = Timer(120,run_clipclient) 
    else:
        #we have session key, so...
        #check if there's anything here on the clipboard that needs to be copied over...
        current_local_clip = pyperclip.paste()
        if local_clipboard != current_local_clip:
            #there's new stuff on the local clip to copy over...
            #First, secure the paste...
            secure_local_clip = encrypt_clip(current_local_clip,encryption_conf)
            #then send
            r = requests.request('post',"%s/paste" % conf['server']['url'], data = {'session' : session_key , 'paste' : secure_local_clip })
            rj = r.json
            if r.ok and rj['status'] == 200:
                logging.debug('Copying to Global Clipboard Successful : %s' % rj['payload'])
                local_clipboard = current_local_clip
                t = Timer(10,run_clipclient) 
            else:
                logging.error('Copying to Global Clipboard Failed : %s' % rj['payload'])
                t = Timer(30,run_clipclient) 
        else:
            logging.debug("Local Clip : old")
            t = Timer(10,run_clipclient) 

        #get the remote / global clip contents if any...
        r = requests.request('post',"%s/copy" % conf['server']['url'], data = {'session' : session_key })
        rj = r.json
        if r.ok and rj['status'] == 200:
            secure_global_clipboard = rj['payload']
            #First, decrypt the stuff...
            global_clipboard = decrypt_clip(secure_global_clipboard,encryption_conf)
            if global_clipboard != local_clipboard:
                pyperclip.copy(global_clipboard)
                local_clipboard = global_clipboard
                logging.info("Found new Data on Global Clip from IP : %s, Size : %s" % (rj['source_ip'],rj['size'])) 
                t = Timer(10,run_clipclient) 
            else:
                logging.debug("Global Clip : old")
                t = Timer(10,run_clipclient) 
        else:
            logging.error("Failed to Read Global Clip : %s " % rj['payload'])
            t = Timer(30,run_clipclient) 

    if t:
        t.start() #schedule next run of the clip client...

run_clipclient() #start clip client
