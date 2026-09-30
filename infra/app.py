import os
import aws_cdk as cdk
from certflow_stack import CertflowStack

app = cdk.App()

CertflowStack(
    app,
    "CertflowStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
)

app.synth()
