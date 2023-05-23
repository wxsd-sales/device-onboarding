############################################################
# Dockerfile to build Device Onboarding Manager
############################################################
#sudo docker build -t device-onboarding .
#docker run -i --env-file .env -v /Users/tahanson/Documents/sales/device-onboarding/config_files/:/config_files -t device-onboarding
###########################################################################

FROM python:3.8.1

# File Author / Maintainer
MAINTAINER "Taylor Hanson <tahanson@cisco.com>"

# Copy the application folder inside the container
ADD . .

# Set the default directory where CMD will execute
WORKDIR /

# Get pip to download and install requirements:
RUN pip install aiohttp
RUN pip install python-dotenv
RUN pip install playwright
RUN playwright install

#Copy environment variables file. Overwrite it with prod.env if prod.env exists.
#COPY .env prod.env* .env


# Set the default command to execute
# when creating a new container
CMD ["python","main.py"]
