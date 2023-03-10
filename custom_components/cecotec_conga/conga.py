import logging
import random
import requests
import string
import json
import boto3
import datetime
from pycognito import Cognito
from pycognito.utils import RequestsSrpAuth

_LOGGER = logging.getLogger(__name__)

CECOTEC_API_BASE_URL = "https://qafbskf2ug.execute-api.eu-west-2.amazonaws.com"
AWS_IOT_ENDPOINT = "https://a39k27k2ztga9m-ats.iot.eu-west-2.amazonaws.com"


class Conga:
    def __init__(self, username, password):
        self._username = username
        self._password = password
        self._devices = []
        self._shadow = {}
        self._tactics = {}
        self._api_token = None
        self._iot_client = None
        self._iot_token_expiration = None

    def list_vacuums(self):
        self._refresh_api_token()
        devices = requests.post(
            f"{CECOTEC_API_BASE_URL}/api/user_machine/list",
            json={},
            auth=self._api_token,
        )
        devices.raise_for_status()
        self._devices = devices.json()["data"]["page_items"]
        _LOGGER.warn(self._devices)
        return self._devices

    def list_plans(self):
        return self._plan_names

    def update_shadows(self, sn):
        self._refresh_iot_client()
        shadow = self._iot_client.get_thing_shadow(thingName=sn)
        shadow_service = self._iot_client.get_thing_shadow(
            thingName=sn, shadowName="service"
        )

        shadow = json.load(shadow["payload"])["state"]["reported"]
        shadow_service = json.load(shadow_service["payload"])["state"]["reported"]

        self._shadow = shadow
        self._tactics = shadow_service["getTimeTactics"]["body"]["timeTactics"]

        # Fill plans variables
        plans = []
        plan_names = []
        for tactic in json.loads(self._tactics)["value"]:
            if "planName" in tactic:
                plans.append(tactic)
                plan_names.append(tactic["planName"])

        self._plans = plans
        self._plan_names = plan_names

        return self._shadow

    def get_status(self):
        return self._shadow

    def start(self, sn, fan_speed):
        payload = {
            "state": {
                "desired": {
                    "startClean": {
                        "state": 1,
                        "body": {
                            "mode": "Auto",
                            "deepClean": 0,
                            "fanLevel": fan_speed,
                            "water": 1,
                            "autoBoost": 0,
                            "params": "[]",
                        },
                    }
                }
            }
        }

        self._send_payload(sn, payload)

    def set_fan_speed(self, sn, level):
        payload = {"state": {"desired": {"workNoisy": level}}}
        _LOGGER.debug(payload)
        self._refresh_iot_client()
        self._iot_client.update_thing_shadow(
            thingName=sn, payload=bytes(json.dumps(payload), "ascii")
        )

    def set_water_level(self, sn, level):
        payload = {"state": {"desired": {"water": level}}}
        _LOGGER.debug(payload)
        self._refresh_iot_client()
        self._iot_client.update_thing_shadow(
            thingName=sn, payload=bytes(json.dumps(payload), "ascii")
        )

    def start_plan(self, sn, plan_name):
        _LOGGER.info(f"Starting plan {plan_name} on {sn}")
        allowed_chars = string.ascii_lowercase + string.ascii_uppercase + string.digits
        result_str = "".join(random.choice(allowed_chars) for i in range(10))
        plan = self._get_plan_details(plan_name)
        payload = {
            "state": {
                "desired": {
                    "StartTimedCleanTask": {
                        "id": result_str,
                        "params": json.dumps(plan),
                    }
                }
            }
        }
        _LOGGER.warn(payload)
        self._send_payload(sn, payload)

    def home(self, sn):
        payload = {"state": {"desired": {"startFindCharge": {"state": 1}}}}

        self._send_payload(sn, payload)

    def _get_plan_details(self, plan_name):
        _LOGGER.debug(f"Looking for plan {plan_name}")
        _LOGGER.debug(f"Plans: {self._plans}")
        for plan in self._plans:
            if plan["planName"] == plan_name:
                return plan
        return ""

    def _send_payload(self, sn, payload):
        _LOGGER.debug(payload)
        self._refresh_iot_client()
        self._iot_client.update_thing_shadow(
            thingName=sn,
            shadowName="service",
            payload=bytes(json.dumps(payload), "ascii"),
        )

    def _refresh_api_token(self):
        if self._api_token != None:
            return self._api_token

        self._api_token = RequestsSrpAuth(
            username=self._username,
            password=self._password,
            user_pool_id="eu-west-2_L5T0M5yrf",
            client_id="6iep27ce22ojt8bgb2vji3d387",
            user_pool_region="eu-west-2",
        )

    def _refresh_iot_client(self):
        if (
            self._iot_client != None
            and self._iot_token_expiration != None
            and datetime.datetime.now().timestamp()
            < self._iot_token_expiration.timestamp()
        ):
            return self._iot_client

        _LOGGER.info("Refreshing Cecotec Conga token")

        u = Cognito(
            "eu-west-2_L5T0M5yrf", "6iep27ce22ojt8bgb2vji3d387", username=self._username
        )
        u.authenticate(password=self._password)
        cognito = boto3.client("cognito-identity", "eu-west-2")
        response = cognito.get_id(
            IdentityPoolId="eu-west-2:0cdeb155-55bb-45f8-9710-4895bd40d605",
            Logins={
                "cognito-idp.eu-west-2.amazonaws.com/eu-west-2_L5T0M5yrf": u.id_token
            },
        )
        creds = cognito.get_credentials_for_identity(
            IdentityId=response["IdentityId"],
            Logins={
                "cognito-idp.eu-west-2.amazonaws.com/eu-west-2_L5T0M5yrf": u.id_token
            },
        )
        self._iot_client = boto3.client(
            "iot-data",
            region_name="eu-west-2",
            endpoint_url=AWS_IOT_ENDPOINT,
            aws_access_key_id=creds["Credentials"]["AccessKeyId"],
            aws_secret_access_key=creds["Credentials"]["SecretKey"],
            aws_session_token=creds["Credentials"]["SessionToken"],
        )
        self._iot_token_expiration = creds["Credentials"]["Expiration"]
