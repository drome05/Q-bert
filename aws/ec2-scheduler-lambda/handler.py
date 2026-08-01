import os
import time
import boto3

INSTANCE_ID = os.environ["INSTANCE_ID"]
IP_PARAMETER_NAME = os.environ.get("IP_PARAMETER_NAME", "/discord-bot/current-ip")

ec2 = boto3.client("ec2")
ssm = boto3.client("ssm")


def handler(event, context):
    action = event.get("action")

    if action == "stop":
        ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        return {"status": "stopping"}

    if action == "start":
        ec2.start_instances(InstanceIds=[INSTANCE_ID])
        waiter = ec2.get_waiter("instance_running")
        waiter.wait(InstanceIds=[INSTANCE_ID], WaiterConfig={"Delay": 5, "MaxAttempts": 24})

        # Public IP isn't always attached the instant state flips to running -- give it
        # a few retries rather than failing the whole scheduled run over a race condition.
        ip = None
        for _ in range(6):
            resp = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
            ip = resp["Reservations"][0]["Instances"][0].get("PublicIpAddress")
            if ip:
                break
            time.sleep(5)

        if ip:
            ssm.put_parameter(Name=IP_PARAMETER_NAME, Value=ip, Type="String", Overwrite=True)

        return {"status": "started", "ip": ip}

    raise ValueError(f"Unknown action: {action!r}")
