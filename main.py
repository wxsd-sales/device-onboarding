from __future__ import print_function,unicode_literals
from tabnanny import check
#from os import access
import aiohttp
import asyncio
import logging
import os
import socket
import sys
import time
import traceback

from playwright.async_api import async_playwright

from inspect import trace
from lib.spark_asyncio import Spark
from lib.settings import Settings, CustomFormatter


class LogRecord(logging.LogRecord):
    def getMessage(self):
        msg = self.msg
        if self.args:
            if isinstance(self.args, dict):
                msg = msg.format(**self.args)
            else:
                msg = msg.format(*self.args)
        return msg

logging.setLogRecordFactory(LogRecord)
logger = logging.getLogger("DeviceOnboarding")
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
ch.setFormatter(CustomFormatter())
logger.addHandler(ch)

class SparkObj(object):
    s = None

async def refresh_token():
    async with aiohttp.ClientSession(headers={"Content-Type":"application/json"}) as session:
        data = {
            "grant_type":"refresh_token",
            "client_id":Settings.client_id,
            "client_secret":Settings.client_secret,
            "refresh_token":Settings.refresh_token
        }
        async with session.post("https://webexapis.com/v1/access_token", json=data) as resp:
            res = await resp.json()
            #print(resp.headers)
            #print(resp.status)
            logger.info(res)
            return res

async def user_management_add(device, username, password):
    data_args = { "Active": "True", "ClientCertificateDN": '', "Passphrase": password, "PassphraseChangeRequired": "False", 
                    "Role": ['Admin', 'User', 'Audit'], "ShellLogin": "True", "Username": username }
    data = { "deviceId":device["id"], "arguments": data_args }
    add_user_res = await SparkObj.s.post("https://webexapis.com/v1/xapi/command/UserManagement.User.Add", data)
    if "command failed" in add_user_res.get('message',"").lower():
        logger.error(add_user_res.get('message'))
        sys.exit()
    else:
        logger.info("User '{0}' added.".format(username))

async def user_management_delete(device, username):
    data_args = { "Username": username }
    data = { "deviceId":device["id"], "arguments": data_args }
    delete_user_res = await SparkObj.s.post("https://webexapis.com/v1/xapi/command/UserManagement.User.Delete", data)
    #print("delete_user_res:{0}".format(delete_user_res))
    logger.info("User '{0}' deleted.".format(username))

async def manage_users(device, add_users):
    data = { "deviceId":device["id"], "arguments": {"Limit":100, "Offset":0} }
    users = await SparkObj.s.post("https://webexapis.com/v1/xapi/command/UserManagement.User.List", data)
    logger.debug('Users:{0}'.format(users))
    delete_users = []
    for user in users['result']['User']:
        delete_users.append(user['Username'])
    for user in add_users:
        if user != "admin":
            if user in delete_users:
                logger.warning("User '{0}' already exists.  Deleting and recreating it.".format(user))
                await user_management_delete(device, user)
            await user_management_add(device, user, add_users[user])

async def device_configuration(device, configfile):
    filelines = []
    with open(configfile, 'r') as f:
        filelines = f.readlines()

    config_resp = await SparkObj.s.get("https://webexapis.com/v1/deviceConfigurations?deviceId={0}".format(device["id"]))

    data = []
    for line in filelines:
        if not (line.lower().startswith('sep=') or line.lower().startswith('configuration name,')):
            parts = line.split(',')
            config_key = parts[0].strip()
            if config_key in config_resp.get('items',[]):
                data.append({
                    "op":"replace",
                    "path":"{0}/sources/configured/value".format(config_key),
                    "value":parts[1].strip()
                })
            else:
                logger.warning('{0} not supported on this device.'.format(config_key))
    logger.debug('Configuration Data:')
    logger.debug(data)
    config_set_resp = await SparkObj.s.patch("https://webexapis.com/v1/deviceConfigurations?deviceId={0}".format(device["id"]), data, {"Content-Type":"application/json-patch+json"})

    
def check_connection(device_ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    connected = False
    try:
        s.connect((device_ip,80))
        connected = True
    except Exception as e:
        pass
    s.close()
    return connected
    
class PlaywrightManager(object):
    timeout = 10000

    @classmethod
    async def sign_in(cls, device_ip, username, password):
        await cls.page.goto("https://{0}".format(device_ip), timeout=cls.timeout)
        logger.debug("PlaywrightManager.sign_in - {0}".format(await cls.page.title()))
        await cls.page.locator("#username").fill(username)
        await cls.page.locator('input[name="password"]').fill(password)
        logger.debug("PlaywrightManager.sign_in - {0}".format(cls.page.get_by_role("button")))
        await cls.page.get_by_role("button").click()
        logger.info('PlaywrightManager.sign_in - clicked sign in button')
        await cls.page.wait_for_url("https://{0}/web".format(device_ip), timeout=cls.timeout)
        logger.debug("PlaywrightManager.sign_in - {0}".format(await cls.page.title()))
        return True

    @classmethod
    async def update_users(cls, device_ip, password):
        try:
            await cls.page.goto("https://{0}/web/users/edit/admin".format(device_ip), timeout=cls.timeout)
            logger.info("PlaywrightManager.update_users - editing admin user")
            await cls.page.get_by_label("Active", exact=True).click()
            await cls.page.locator('input[name="passphraseChangeRequired"]').set_checked(False)
            await cls.page.locator('input[name="yourPassphrase"]').nth(0).fill(password)
            logger.info('PlaywrightManager.update_users - set active and unchecked passphrase change required, and entered our passphrase.')
            await cls.page.get_by_text('Save').click()
            logger.info('PlaywrightManager.update_users - activated admin')
            await cls.page.locator('input[name="passphrase1"]').fill(password)
            await cls.page.locator('input[name="passphrase2"]').fill(password)
            await cls.page.locator('input[name="yourPassphrase"]').nth(1).fill(password)
            await cls.page.get_by_text('Change Passphrase').click()
            logger.info('PlaywrightManager.update_users - updated admin password.')
            return True
        except Exception as e:
            traceback.print_exc()
            return False

    @classmethod
    async def upload_macro(cls, device_ip, macrofile):
        macroname = os.path.split(macrofile)[1].replace('.js','').replace('.','')
        macro_count = 0
        try:
            await cls.page.goto("https://{0}/web/macros".format(device_ip), timeout=cls.timeout)
            enable_macros = cls.page.get_by_text('Enable Macros')
            enable_count = await enable_macros.count()
            if enable_count > 0:
                await enable_macros.click()
            else: 
                try:
                    await cls.page.wait_for_selector('span[title="{0}"]'.format(macroname), timeout=2000)
                    macro_count = await cls.page.locator('span[title="{0}"]'.format(macroname)).count()
                except Exception as e:
                    logger.debug('PlaywrightManager.upload_macro - {0} macro not found'.format(macroname))
            
            if macro_count == 0:
                async with cls.page.expect_file_chooser() as fc_info:
                    await cls.page.locator('input[id="invisible-read-file-input"]').click()
                file_chooser = await fc_info.value
                logger.debug('PlaywrightManager.upload_macro - file_chooser found:{0}'.format(file_chooser))
                logger.debug('PlaywrightManager.upload_macro - waiting a second to prevent site issues with file upload.')
                await asyncio.sleep(1)
                await file_chooser.set_files(macrofile)
                logger.info('PlaywrightManager.upload_macro - uploading macro...')
                await cls.page.locator('.macro.row.selected.disabled > span > svg').click()
                await cls.page.locator('.macro.row.selected.disabled > div > div.toggle-button').click()
                logger.info('PlaywrightManager.upload_macro - enabled macro...')
                await asyncio.sleep(1)
            else:
                logger.info('PlaywrightManager.upload_macro - {0} macro already exists.'.format(macroname))
            return True
        except Exception as e:
            traceback.print_exc()
            return False

    @classmethod
    async def upload_extension(cls, device_ip, extensionfile):
        extensionname = os.path.split(extensionfile)[1].replace('.xml','')
        try:
            extension_count = 0
            await cls.page.goto("https://{0}/web/roomcontrol".format(device_ip), timeout=cls.timeout)

            try:
                await cls.page.wait_for_selector('.panel-row', timeout=2000)
                extension_count = await cls.page.locator('.panel-row').count()
            except Exception as e:
                logger.debug('PlaywrightManager.upload_extension - {0} extension not found'.format(extensionname))

            if extension_count == 0:
                await cls.page.get_by_title('Editor menu').click()
                async with cls.page.expect_file_chooser() as fc_info:
                    await cls.page.locator('.item.menu-import.import-from-file').click()
                file_chooser = await fc_info.value
                logger.debug('PlaywrightManager.upload_extension - file_chooser found:{0}'.format(file_chooser))
                logger.debug('PlaywrightManager.upload_extension - waiting a second to prevent site issues with file upload.')
                await asyncio.sleep(1)
                await file_chooser.set_files(extensionfile)
                logger.info('PlaywrightManager.upload_extension - uploading extension...')
                version_mismatch = cls.page.locator('div.dialog > div.body')
                if await version_mismatch.count() > 0:
                    logger.info("PlaywrightManager.upload_extension - {0}".format(await version_mismatch.inner_text()))
                    await cls.page.get_by_text("Ok").click()
                    logger.info('PlaywrightManager.upload_extension - exporting extension...')
                    await cls.page.locator('.round-button.export').click()
                    logger.info('PlaywrightManager.upload_extension - enabled extension.')
                    await asyncio.sleep(1)
            else:
                logger.info('PlaywrightManager.upload_extension - {0} extension already exists.'.format(extensionname))
            return True
        except Exception as e:
            traceback.print_exc()
            return False


    @classmethod
    async def run(cls, device_ip, username, password, macrofile, extensionfile):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            cls.page = await browser.new_page(ignore_https_errors=True)

            await cls.sign_in(device_ip, username, password)
            update_users_success = await cls.update_users(device_ip, password)
            upload_macro_success = await cls.upload_macro(device_ip, macrofile)
            upload_extension_success = await cls.upload_extension(device_ip, extensionfile)

            logger.info('Playwright Summary:')
            logger.info('Update Users Success:{0}'.format(update_users_success))
            logger.info('Upload Macro Success:{0}'.format(upload_macro_success))
            logger.info('Upload Extension Success:{0}'.format(upload_extension_success))

            await browser.close()

def get_first_file(extension):
    for _file in os.listdir('config_files'):
        if _file.endswith(extension):
            return _file
    return None

async def main():
    loop_frequency = 60 * 10 #seconds * minutes = sleep seconds
    last_token_refresh = 0
    
    add_users = {"admin": Settings.admin_password,
                 Settings.secondary_username: Settings.secondary_password,
                 Settings.automation_username: Settings.admin_password,}
    systemfiles = {'macro':None, 'extension':None, 'config':None}
    filetypes = {'.js':'macro', '.xml':'extension', '.csv':'config'}
    mount_directory = "config_files"
    devicesfile = os.path.join(mount_directory, os.path.join("devices","list.csv"))
    logger.debug("devicesfile:{0}".format(devicesfile))
    for extension in filetypes:
        first_file = get_first_file(extension)
        if first_file != None:
            systemfiles[filetypes[extension]] = os.path.join(mount_directory, first_file)
        else:
            logger.error('No {0} files with a {1} extension exist in {2}'.format(filetypes[extension], extension, mount_directory))
            sys.exit()
    logger.debug(systemfiles)
    
    half_configured_device_ids = [] #devices that have been configured using xAPI and non-same-network commands
    configured_device_ids = [] #devices that have been FULLY configured (including playwright/network requirements)
    if os.path.exists(devicesfile):
        with open(devicesfile, 'r') as df:
            lines = df.readlines()
            for line in lines[1:]:
                clean_line = line.strip()
                if clean_line:
                    parts = clean_line.split(',')
                    configured_device_ids.append(parts[1])
    
    logger.debug('existing configured_device_ids:{0}'.format(configured_device_ids))
    while True:
        sleep_seconds = loop_frequency
        try:
            #refresh token once per day, not per loop
            if time.time() - last_token_refresh > 86400: #86400 seconds in a day
                token_res = await refresh_token()
                access_token = token_res['access_token']
                spark = Spark(access_token)
                SparkObj.s = spark
                last_token_refresh = time.time()

            res = await spark.get("https://webexapis.com/v1/devices")
            if not os.path.exists(devicesfile):
                #Make sure the folder and subfolders exists first
                dirs, filename = os.path.split(devicesfile)
                if not os.path.exists(dirs):
                    os.makedirs(dirs)
                logger.info('Devices file ({0}) does not exist, performing first time device collection.'.format(devicesfile))
                with open(devicesfile, 'w') as df:
                    df.write('Name,DeviceID\n')
                    for device in res["items"]:
                        if(device["type"] == "roomdesk" and device.get('personId') != None):
                            #device.get('personId') will filter to only save/track personal mode devices.
                            df.write('{0},{1}\n'.format(device["displayName"], device["id"]))
                            configured_device_ids.append(device["id"])
                            half_configured_device_ids.append(device["id"])
                logger.info('Initial Device List Configured.')
                sleep_seconds = 20
            else:
                for device in res["items"]:
                    try:
                        if(device["type"] == "roomdesk" and device.get('personId') != None):
                            #results don't include phones, but do include roomdesks and accessories 
                            logger.info("{0}, {1}, {2}".format(device["displayName"], device["connectionStatus"], device["ip"]))
                            if device["connectionStatus"] != "disconnected" and device["id"] not in configured_device_ids:
                                logger.debug("Configuring: {0}".format(device))
                                if device["id"] not in half_configured_device_ids:
                                    await manage_users(device, add_users)
                                    await device_configuration(device, systemfiles['config'])
                                    half_configured_device_ids.append(device["id"])
                                connected = check_connection(device["ip"])
                                if connected:
                                    try:
                                        logger.debug('Device {0} is reachable at {1}'.format(device["displayName"], device["ip"]))
                                        await PlaywrightManager.run(device["ip"], Settings.automation_username, Settings.admin_password, systemfiles['macro'], systemfiles['extension'])
                                        configured_device_ids.append(device['id'])
                                        with open(devicesfile, 'a') as df:
                                            df.write('{0},{1}\n'.format(device["displayName"], device["id"]))
                                        await user_management_delete(device, Settings.automation_username)
                                    except Exception as ex:
                                        traceback.print_exc()
                                        logger.error('Device {0} at {1} encountered the above isolated error.'.format(device["displayName"], device["ip"]))
                                else:
                                    logger.error('Device {0} is NOT reachable at {1}'.format(device["displayName"], device["ip"]))
                    except Exception as exx:
                        traceback.print_exc()
                        logger.error('Device {0} at {1} encountered the above critical error.'.format(device.get("displayName"), device.get("ip")))
        except Exception as e:
            traceback.print_exc()
        logger.debug('Sleeping for {0} seconds.'.format(sleep_seconds))
        await asyncio.sleep(sleep_seconds)

application_path = os.path.dirname(sys.executable)
print('application_path:{0}'.format(application_path))
asyncio.run(main())

