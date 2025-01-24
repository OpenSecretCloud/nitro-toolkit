# Nitro Toolkit

A collection of essential utilities for working with AWS Nitro Enclaves. These tools help manage credentials, logging, networking, and base image creation for Nitro Enclaves.

## Components

### Credential Requester

A Python-based service that securely handles AWS credential management for Nitro Enclaves.

#### Features
- Retrieves AWS credentials using IMDSv2
- Handles SecretsManager requests
- Supports vsock communication with enclaves
- Multi-threaded request handling
- Automatic token refresh

#### Usage
```bash
# Build the Docker image
docker build -t credential-requester .

# Run the container
docker run -d --restart always \
  --name credential-requester \
  --device=/dev/vsock:/dev/vsock \
  -v /var/run/vsock:/var/run/vsock \
  --privileged \
  -e PORT=8003 \
  credential-requester:latest
```

### Enclave Base Image

A foundational Docker image for building AWS Nitro Enclaves libraries and binaries, based on Amazon Linux 2.

#### Features
- Pre-built with essential AWS Nitro Enclave SDK components
- Includes KMS tools and NSM library
- Optimized for minimal size and security
- Built with necessary dependencies for enclave operations

#### Key Components
- AWS Nitro Enclaves SDK (C)
- AWS-LC (Cryptography)
- S2N-TLS
- AWS Common Runtime (CRT) libraries
- JSON-C library
- NSM API library

### Logging

A CloudWatch logging solution specifically designed for Nitro Enclaves.

#### Features
- Forwards logs from enclaves to AWS CloudWatch
- Supports vsock communication
- Multi-threaded log processing
- Automatic retry mechanisms
- Configurable log groups and streams

#### Usage
```bash
# Build the Docker image
docker build -t enclave-logging .

# Run the container
docker run -d --restart always \
  --name enclave-logging \
  --device=/dev/vsock:/dev/vsock \
  -v /var/run/vsock:/var/run/vsock \
  --privileged \
  -e VSOCK_PORT=8011 \
  -e LOG_GROUP=/aws/nitro-enclaves/my-enclave \
  -e LOG_STREAM=enclave-logs \
  -e AWS_REGION=us-east-2 \
  enclave-logging:latest
```

### Traffic Forwarder

A Python utility for forwarding network traffic between Nitro Enclaves and external services.

#### Features
- Bidirectional traffic forwarding
- Support for both TCP and VSOCK protocols
- Automatic reconnection on failure
- Configurable endpoints
- Thread-safe operation

#### Usage
```python
# Forward traffic from local TCP to VSOCK
python traffic_forwarder.py <local_ip> <local_port> <remote_cid> <remote_port>

# Example: Forward from localhost:8080 to enclave CID 3 port 5000
python traffic_forwarder.py 127.0.0.1 8080 3 5000
```

### VSOCK Helper

A utility for managing VSOCK communications with Nitro Enclaves.

#### Features
- Reliable VSOCK communication
- Automatic retry mechanism
- Configurable timeouts
- JSON request/response handling
- Detailed error reporting

#### Usage
```python
# Send a request to an enclave
python vsock_helper.py <cid> <port> <request>

# Example: Send a credentials request
python vsock_helper.py 3 8003 '{"request_type":"credentials","key_name":null}'
```

## Installation

1. Clone the repository:
```bash
git clone https://github.com/your-org/nitro-tools.git
cd nitro-tools
```

2. Each tool can be built and run independently using Docker. See the individual component sections for specific instructions.

## Requirements

- AWS Nitro Enclaves enabled instance
- Docker
- Python 3.9+
- AWS CLI configured with appropriate permissions
- Proper IAM roles and policies configured for AWS services
