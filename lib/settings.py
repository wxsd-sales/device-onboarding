import logging 
import os
from dotenv import load_dotenv

load_dotenv()

print(os.environ)
class Settings(object):
	automation_username = "automation"
	refresh_token = os.environ["MY_WEBEX_REFRESH_TOKEN"].replace('"','')
	client_id = os.environ["MY_WEBEX_CLIENT_ID"].replace('"','')
	client_secret = os.environ["MY_WEBEX_SECRET"].replace('"','')
	
	admin_password = os.environ["ADMIN_ACCOUNT_PASSWORD"].replace('"','')

	secondary_username = os.environ["SECONDARY_USERNAME"].replace('"','')
	secondary_password = os.environ["SECONDARY_ACCOUNT_PASSWORD"].replace('"','')

class CustomFormatter(logging.Formatter):

    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    blue = "\x1b[31;34m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"

    FORMATS = {
        logging.DEBUG: blue + format + reset,
        logging.INFO: grey + format + reset,
        logging.WARNING: yellow + format + reset,
        logging.ERROR: red + format + reset,
        logging.CRITICAL: bold_red + format + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)
