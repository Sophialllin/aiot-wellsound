import boto3
import os

sns = boto3.client('sns')

TOPIC_ARN = os.environ["SNS_WELLSOUND_ARN"]

NOTIFY_LABELS = ["Disruptive", "Focus", "Collaborative","Lively"]

def lambda_handler(event, context):
    for record in event["Records"]:
        if record["eventName"] != "INSERT":
            continue

        new_image = record["dynamodb"]["NewImage"]
        
        label = new_image["aq_label"]["S"]
        room_id = new_image["room_id"]["S"]
        room_name = new_image["room_name"]["S"]
        confidence = new_image["confidence"]["N"]
        timestamp = new_image["timestamp"]["N"]

        if label not in NOTIFY_LABELS:
            continue

        sns.publish(
            TopicArn=TOPIC_ARN,
            Message=f"Room {room_name} (ID: {room_id}) has been classified as {label}.\nConfidence: {confidence}\nTimestamp: {timestamp}",
            Subject=f"WellSound Alert: {label} Environment Detected",
            MessageAttributes={
                "aq_label": {
                    "DataType": "String",
                    "StringValue": label
                }
            }
        )

    return {"statusCode": 200}