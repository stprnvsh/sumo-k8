"""Setup script for sumo-k8-client package."""

from setuptools import setup, find_packages

setup(
    name="sumo-k8-client",
    version="1.0.0",
    description="Python client for SUMO-K8 Kubernetes simulation controller",
    author="Transcality",
    author_email="info@transcality.com",
    url="https://github.com/transcality/sumo-k8",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "requests>=2.28.0",
    ],
    extras_require={
        "aws": [
            "boto3>=1.28.0",
        ],
        "gcp": [
            "google-cloud-container>=2.0.0",
        ],
        "azure": [
            "azure-mgmt-containerservice>=20.0.0",
            "azure-identity>=1.12.0",
        ],
        "all-clouds": [
            "boto3>=1.28.0",
            "google-cloud-container>=2.0.0",
            "azure-mgmt-containerservice>=20.0.0",
            "azure-identity>=1.12.0",
        ],
        "dev": [
            "pytest>=7.0.0",
            "responses>=0.23.0",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
