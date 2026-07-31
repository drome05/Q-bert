Stops/starts the bot's EC2 instance on a schedule (see the main README's
"Deployment: AWS" section for the why). Deployed by hand, not via CI --
this is account-level automation, not something the cluster reconciles.

## Deploy

```bash
zip lambda.zip handler.py

aws iam create-role --role-name discord-bot-ec2-scheduler-role \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
  }'

aws iam put-role-policy --role-name discord-bot-ec2-scheduler-role \
  --policy-name ec2-start-stop \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {"Effect": "Allow", "Action": ["ec2:StartInstances", "ec2:StopInstances", "ec2:DescribeInstances"], "Resource": "*"},
      {"Effect": "Allow", "Action": ["ssm:PutParameter"], "Resource": "arn:aws:ssm:us-east-1:952151418421:parameter/discord-bot/current-ip"},
      {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], "Resource": "arn:aws:logs:us-east-1:952151418421:*"}
    ]
  }'

aws lambda create-function \
  --function-name discord-bot-ec2-scheduler \
  --runtime python3.12 \
  --role arn:aws:iam::952151418421:role/discord-bot-ec2-scheduler-role \
  --handler handler.handler \
  --zip-file fileb://lambda.zip \
  --timeout 120 \
  --region us-east-1

aws iam create-role --role-name discord-bot-scheduler-invoke-role \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "scheduler.amazonaws.com"}, "Action": "sts:AssumeRole"}]
  }'

aws iam put-role-policy --role-name discord-bot-scheduler-invoke-role \
  --policy-name invoke-lambda \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": "arn:aws:lambda:us-east-1:952151418421:function:discord-bot-ec2-scheduler"}]
  }'

aws scheduler create-schedule \
  --name discord-bot-stop-4am \
  --schedule-expression "cron(0 4 * * ? *)" \
  --schedule-expression-timezone "America/Chicago" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target '{
    "Arn": "arn:aws:lambda:us-east-1:952151418421:function:discord-bot-ec2-scheduler",
    "RoleArn": "arn:aws:iam::952151418421:role/discord-bot-scheduler-invoke-role",
    "Input": "{\"action\":\"stop\"}"
  }'

aws scheduler create-schedule \
  --name discord-bot-start-9pm \
  --schedule-expression "cron(0 21 * * ? *)" \
  --schedule-expression-timezone "America/Chicago" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target '{
    "Arn": "arn:aws:lambda:us-east-1:952151418421:function:discord-bot-ec2-scheduler",
    "RoleArn": "arn:aws:iam::952151418421:role/discord-bot-scheduler-invoke-role",
    "Input": "{\"action\":\"start\"}"
  }'
```

## Manual override

```bash
aws lambda invoke --function-name discord-bot-ec2-scheduler \
  --payload '{"action":"start"}' --cli-binary-format raw-in-base64-out \
  --region us-east-1 /tmp/out.json && cat /tmp/out.json
```
