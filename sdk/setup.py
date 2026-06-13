from setuptools import setup, find_packages

setup(
    name="openwec",
    version="0.1.0a1",
    description="Python SDK for OpenWEC — endurance racing data (WEC, ELMS, ALMS, Le Mans Cup, IMSA)",
    packages=find_packages(),
    install_requires=[
        "requests>=2.28",
        "pandas>=2.0",
    ],
    extras_require={
        "plotting": ["matplotlib>=3.7"],
    },
    python_requires=">=3.10",
)