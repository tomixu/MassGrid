#!/user/bin python
#coding:utf-8
import commands    
import os    
import time
import csv
import sys
import string
import getpass
import logging
from itertools import islice

import xlrd
import json

# ***************** authproxy ****************************

"""
  Copyright 2011 Jeff Garzik

  AuthServiceProxy has the following improvements over python-jsonrpc's
  ServiceProxy class:

  - HTTP connections persist for the life of the AuthServiceProxy object
    (if server supports HTTP/1.1)
  - sends protocol 'version', per JSON-RPC 1.1
  - sends proper, incrementing 'id'
  - sends Basic HTTP authentication headers
  - parses all JSON numbers that look like floats as Decimal
  - uses standard Python json lib

  Previous copyright, from python-jsonrpc/jsonrpc/proxy.py:

  Copyright (c) 2007 Jan-Klaas Kollhof

  This file is part of jsonrpc.

  jsonrpc is free software; you can redistribute it and/or modify
  it under the terms of the GNU Lesser General Public License as published by
  the Free Software Foundation; either version 2.1 of the License, or
  (at your option) any later version.

  This software is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU Lesser General Public License for more details.

  You should have received a copy of the GNU Lesser General Public License
  along with this software; if not, write to the Free Software
  Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
"""

try:
    import http.client as httplib
except ImportError:
    import httplib
import base64
import decimal
import json
import logging
try:
    import urllib.parse as urlparse
except ImportError:
    import urlparse

USER_AGENT = "AuthServiceProxy/0.1"

HTTP_TIMEOUT = 30

log = logging.getLogger("BitcoinRPC")

class JSONRPCException(Exception):
    def __init__(self, rpc_error):
        parent_args = []
        try:
            parent_args.append(rpc_error['message'])
        except:
            pass
        Exception.__init__(self, *parent_args)
        self.error = rpc_error
        self.code = rpc_error['code'] if 'code' in rpc_error else None
        self.message = rpc_error['message'] if 'message' in rpc_error else None

    def __str__(self):
        return '%d: %s' % (self.code, self.message)

    def __repr__(self):
        return '<%s \'%s\'>' % (self.__class__.__name__, self)


def EncodeDecimal(o):
    if isinstance(o, decimal.Decimal):
        return float(round(o, 8))
    raise TypeError(repr(o) + " is not JSON serializable")

class AuthServiceProxy(object):
    __id_count = 0

    def __init__(self, service_url, service_name=None, timeout=HTTP_TIMEOUT, connection=None):
        self.__service_url = service_url
        self.__service_name = service_name
        self.__url = urlparse.urlparse(service_url)
        if self.__url.port is None:
            port = 80
        else:
            port = self.__url.port
        (user, passwd) = (self.__url.username, self.__url.password)
        try:
            user = user.encode('utf8')
        except AttributeError:
            pass
        try:
            passwd = passwd.encode('utf8')
        except AttributeError:
            pass
        authpair = user + b':' + passwd
        self.__auth_header = b'Basic ' + base64.b64encode(authpair)

        self.__timeout = timeout

        if connection:
            # Callables re-use the connection of the original proxy
            self.__conn = connection
        elif self.__url.scheme == 'https':
            self.__conn = httplib.HTTPSConnection(self.__url.hostname, port,
                                                  timeout=timeout)
        else:
            self.__conn = httplib.HTTPConnection(self.__url.hostname, port,
                                                 timeout=timeout)

    def __getattr__(self, name):
        if name.startswith('__') and name.endswith('__'):
            # Python internal stuff
            raise AttributeError
        if self.__service_name is not None:
            name = "%s.%s" % (self.__service_name, name)
        return AuthServiceProxy(self.__service_url, name, self.__timeout, self.__conn)

    def __call__(self, *args):
        AuthServiceProxy.__id_count += 1

        log.debug("-%s-> %s %s"%(AuthServiceProxy.__id_count, self.__service_name,json.dumps(args, default=EncodeDecimal)))
        postdata = json.dumps({'version': '1.1',
                               'method': self.__service_name,
                               'params': args,
                               'id': AuthServiceProxy.__id_count}, default=EncodeDecimal)
        self.__conn.request('POST', self.__url.path, postdata,
                            {'Host': self.__url.hostname,
                             'User-Agent': USER_AGENT,
                             'Authorization': self.__auth_header,
                             'Content-type': 'application/json'})
        self.__conn.sock.settimeout(self.__timeout)

        response = self._get_response()
        if response.get('error') is not None:
            raise JSONRPCException(response['error'])
        elif 'result' not in response:
            raise JSONRPCException({
                'code': -343, 'message': 'missing JSON-RPC result'})
        
        return response['result']

    def batch_(self, rpc_calls):
        """Batch RPC call.
           Pass array of arrays: [ [ "method", params... ], ... ]
           Returns array of results.
        """
        batch_data = []
        for rpc_call in rpc_calls:
            AuthServiceProxy.__id_count += 1
            m = rpc_call.pop(0)
            batch_data.append({"jsonrpc":"2.0", "method":m, "params":rpc_call, "id":AuthServiceProxy.__id_count})

        postdata = json.dumps(batch_data, default=EncodeDecimal)
        log.debug("--> "+postdata)
        self.__conn.request('POST', self.__url.path, postdata,
                            {'Host': self.__url.hostname,
                             'User-Agent': USER_AGENT,
                             'Authorization': self.__auth_header,
                             'Content-type': 'application/json'})
        results = []
        responses = self._get_response()
        for response in responses:
            if response['error'] is not None:
                raise JSONRPCException(response['error'])
            elif 'result' not in response:
                raise JSONRPCException({
                    'code': -343, 'message': 'missing JSON-RPC result'})
            else:
                results.append(response['result'])
        return results

    def _get_response(self):
        http_response = self.__conn.getresponse()
        if http_response is None:
            raise JSONRPCException({
                'code': -342, 'message': 'missing HTTP response from server'})

        content_type = http_response.getheader('Content-Type')
        if content_type != 'application/json':
            raise JSONRPCException({
                'code': -342, 'message': 'non-JSON HTTP response with \'%i %s\' from server' % (http_response.status, http_response.reason)})

        responsedata = http_response.read().decode('utf8')
        response = json.loads(responsedata, parse_float=decimal.Decimal)
        if "error" in response and response["error"] is None:
            log.debug("<-%s- %s"%(response["id"], json.dumps(response["result"], default=EncodeDecimal)))
        else:
            log.debug("<-- "+responsedata)
        return response

# ***********************************************************************************************************************


def open_excel(file= ''):
    try:
        data = xlrd.open_workbook(file)
        return data
    except Exception as e:
        print(str(e))

def readExcel(file,by_name='sheet'):#edit table name
	print '****** this is',file,' sheetName:',by_name,'******'
	data = open_excel(file)
	table = data.sheet_by_name(by_name) #get the table
	nrows = table.nrows  # get the total rows
	colnames = table.row_values(0)  # get the 0 row data,for example ['name', 'pwd', 'call']
	lists = []
	for rownum in range(1, nrows): #from the 1 line
	    row = table.row_values(rownum)
	    if row:
	        lists.append(row)
	return lists
    
def Cmd(opt,to,amt):
    if ( to!='' and amt!='' ):
		try:
			famt=float(amt)
			if( opt!='' and opt== '-s'):
				logger.debug(to+"\t"+str(famt))
			else:
				logger.debug('[test] '+to+"\t"+str(famt))
			#raw_input('Continue...')
			if( opt!='' and opt== '-s'):
				logger.debug(access.sendtoaddress(to,famt))
			return 0
		except:
			logger.error( "\n---An error occurred---\n")
			return 1
    else:
		return 2


# one input one output
def sendtoaddress(opt,data):
    logger.debug('sendtoaddress...\n')
    for i in data:
        if Cmd(opt,i[0],i[1])!=0 :
            logger.error("run error")
            exit()
    logger.debug("successed")


# one input many output
def sendmany(opt,label,data):
    logger.debug('sendmany...\n')
    dicts={}
    count=0
    for i in data:
        dicts[i[0]]=i[1]
        count=count+1
        if( opt!= '-s'):
            logger.debug('[test] '+i[0]+"\t"+str(i[1]))

    if( count != len(dicts)):
        logger.debug("Warning duplicated address!")
        exit()
    #print strs
    if( opt!='' and opt== '-s'):
		try:
			logger.debug(access.sendmany(label,dicts))
		except:
			logger.error( "\n---sendmany error occurred---\n")
			return 1
    else:
		return 2
    logger.debug("successed")

if __name__=='__main__':
	# ===== BEGIN LOG SETTINGS =====
    logger = logging.getLogger("sendmanyautotransferxls.py")
    formatter = logging.Formatter('%(asctime)s %(levelname)-8s: %(message)s')
    file_handler = logging.FileHandler("debug.log")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler(sys.stdout)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.setLevel(logging.DEBUG)
    # ===== BEGIN USER SETTINGS =====
    # if you do not set these you will be prompted for a password for every command
    rpcuser = "user"
    rpcpass = "pwd"
    # ====== END USER SETTINGS ======
    if( len(sys.argv)<2):
        logger.debug('Warning ,please input excel filename!')
        sys.exit()
    csvpath=sys.argv[1]
    # -s sendmany
    opt=''
    cmd=''
    test=''
    walletlabel=''
    for x in range(2,len(sys.argv)):
        if (sys.argv[x] == '-s'):
            opt = '-s'
        elif ( sys.argv[x] == '-sendmany' ):
            cmd = '-sendmany'
            if ( x+1 < len(sys.argv) and 0 < len(sys.argv[x+1]) and sys.argv[x+1][0]!='-'):
                walletlabel=sys.argv[x+1]
        elif  ( sys.argv[x] == '-sendtoaddress' ):
            cmd = '-sendtoaddress'
        elif ( sys.argv[x] == '-t'):
            test = '-t'
        elif ( sys.argv[x-1]=='-sendmany' ):
            continue
        else:
            logger.debug('Warning，bad parameter!')
            sys.exit()
        
    if( test!='' and test =='-t' ):
    	if rpcpass == "":
    		access = AuthServiceProxy("http://127.0.0.1:19442")
    	else:
    		access = AuthServiceProxy("http://"+rpcuser+":"+rpcpass+"@127.0.0.1:19442")
    else:
        if rpcpass == "":
            access = AuthServiceProxy("http://127.0.0.1:9442")
        else:
    		access = AuthServiceProxy("http://"+rpcuser+":"+rpcpass+"@127.0.0.1:9442")
    # ==============================================================
    
    #tableName=csvpath[:csvpath.find('.')] # get before of the  last '.' string
    data=[]
    data = readExcel(csvpath)
    if ( cmd!='' and cmd == "-sendmany"):
        sendmany(opt,walletlabel,data)
    elif ( cmd!='' and cmd == "-sendtoaddress"):
            sendtoaddress(opt,data)
    else:
        logger.debug('Warning，please specific mode!')