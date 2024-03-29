# This script is executed as - python websocket-client.py {switchNotificatonURL} {topicURI(s)}
# Example of executing the script with multiple topics:
# python websocket-client.py
# "wss://{ipAddress}:{port}/rest/v1/notification"
# "/rest/v1/system/interfaces/1%2F1%2F1"
# "/rest/v1/system/interfaces/1%2F1%2F2"
##
# noinspection PyUnresolvedReferences
import json
import sys
import traceback
from ssl import PROTOCOL_SSLv23

import requests
from requests import post, get, session
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from tornado import escape
from tornado import gen
from tornado.httpclient import HTTPRequest
from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

USER = '<cx_username>'
PASSWORD = '<cx_password>'

PROXY_DICT = {'http': None, 'https': None}
REQUEST_TIMEOUT = 50
CONNECT_TIMEOUT = 50


class Client(object):
    def __init__(self, url, timeout, topics_list):
        self.url = url
        self.timeout = timeout
        self.ioloop = IOLoop.instance()
        self.ws = None
        self.cookie_header = self.login()
        self.count = 0
        self.connect(url, self.cookie_header, topics_list)
        self.ioloop.start()

    @gen.coroutine
    def connect(self, ws_uri, cookie_header, topics_list):
        print("trying to connect")
        try:
            http_request = HTTPRequest(url=ws_uri, headers=cookie_header, follow_redirects=True,
                                       ssl_options={"ssl_version": PROTOCOL_SSLv23}, validate_cert=False)
            self.ws = yield websocket_connect(http_request)
        except Exception as e:
            print("connection error" + str(e))
        else:
            print("connected")
            self.run(topics_list)

    @gen.coroutine
    def run(self, topics_list):
        json_dict = self.create_json_dict(topics_list)
        self.ws.write_message(escape.utf8(json.dumps(json_dict)))
        while True:
            msg = yield self.ws.read_message()
            self.count = self.count + 1
            if self.count == 1:
                print("Reponse à la subscription : \n{}".format(msg))
                msg_in_json = self.check_if_json(msg)
                if msg_in_json is not None:
                    success_test = self.check_if_success(msg_in_json)
                    if success_test:
                        print("PASS - Initial return JSON")
                    else:
                        print("FAIL - Initial return JSON")
            else:
                dict_json = json.loads(msg)
                port_name = dict_json["data"][0]["resources"][0]['uri'].rsplit("/", 1)[1].replace("%2F", "/")
                if dict_json["data"][0]["resources"][0]["values"]["admin_state"] == 'down':
                    self.msg = "L'interface {} est passé du statut up au statut down ".format(port_name)
                    print(self.msg)
                    self.sendslackapp()
                else:
                    print("L'interface {} est passé du statut down au statut up ".format(port_name))
                    lldp_nei = self.getlldp(dict_json["data"][0]["resources"][0]['uri'])
                    print("Le neighbor LLDP est : {} - Adresse IP : {}".format(
                        lldp_nei[0]["neighbor_info"]['chassis_name'], lldp_nei[0]["neighbor_info"]['mgmt_ip_list']))
                    self.msg = "L'interface {} est passé du statut down au statut up \nLe neighbor LLDP est : {} - Adresse IP : {}".format(
                        port_name, lldp_nei[0]["neighbor_info"]['chassis_name'],
                        lldp_nei[0]["neighbor_info"]['mgmt_ip_list'])
                    self.sendslackapp()
            if msg is None:
                print("connection closed")
                self.ws = None
                break

    def get_subs(self, msg):
        print(msg)
        msg_json = json.loads(msg)
        subs_id = msg_json['subscriber_name']
        print("Subscriber ID : {}".format(subs_id))
        uri_subs = "https://<cx_ip>/rest/v1/system/notification_subscribers/{}?depth=1".format(subs_id)
        return get(uri_subs, headers=self.cookie_header, verify=False)

    def getlldp(self, port):
        url_lldp = "https://<cx_ip>{}/lldp_neighbors?depth=2".format(port)
        get_lldp = get(url_lldp, headers=self.cookie_header, verify=False)
        return get_lldp.json()

    def check_if_json(self, result):
        try:
            msg_json = json.loads(result)
        except ValueError:
            print("The message received is not a valid JSON")
        return msg_json

    def check_if_success(self, json_response):
        pass_type = pass_resource = False
        if "type" in json_response:
            type_msg = json_response["type"]
            if type_msg == "success":
                pass_type = True
        if "data" in json_response:
            for each in json_response['data']:
                if "resources" in each:
                    pass_resource = True
        return pass_type and pass_resource

    def login(self, username=None, password=None, proxies=PROXY_DICT):
        if username is not None:
            assert password is not None, "Must provide password for Login"
        if not username:
            username = USER
        if not password:
            password = PASSWORD

        params = {'username': username, 'password': password}
        login_url = NOTIFICATION_URL.replace("wss", "https")
        login_url = login_url.replace("notification", "login")
        login_header = {'Content-Type': 'application/x-ww-form-urlencoded'}
        response = post(login_url, verify=False, headers=login_header, params=params, proxies=proxies)
        cookie_header = {'Cookie': response.headers['set-cookie']}
        return cookie_header

    def create_json_dict(self, topics_list):
        json_dict = dict()
        json_dict["type"] = "subscribe"
        topic_list = []
        for i in range(len(topics_list)):
            topic_dict = dict()
            topic_dict["name"] = topics_list[i]
            topic_list.append(topic_dict)
        json_dict["topics"] = topic_list
        return json_dict

    def sendslackapp(self):
        slack_url = "https://hooks.slack.com/services/<slackapp_token>"
        payload = {"blocks": [{
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "{}".format(self.msg)
            }
        }]
        }
        header = {'Content-Type': 'application/json'}
        post_slack = requests.post(slack_url, data=json.dumps(payload), headers=header, verify=False)
        if post_slack.status_code == 200:
            print("Message sent successfully to Slack")
        else:
            print("Error while sending message to Slack")


def collect_topics(args):
    topics_list = []
    if len(args) > 2:
        length = len(args)
        for i in range(2, length):
            topics_list.append(args[i])
    return topics_list


if __name__ == "__main__":
    try:
        NOTIFICATION_URL = sys.argv[1]
        topics = collect_topics(sys.argv)
        client = Client(NOTIFICATION_URL, 10, topics)
    except KeyboardInterrupt:
        print("Shutdown requested...exiting")
    except Exception:
        traceback.print_exc(file=sys.stdout)
    sys.exit(0)
