from setuptools import setup, find_packages

setup(
    name="fwrap",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "click>=8.1.0",
        "toml>=0.10.2",
        "pyyaml>=6.0",
        "rich>=13.0.0",
        "python-dotenv>=1.0.0",
        "requests>=2.28.0",
        "eth-hash>=0.5.0",
        "safe-eth-py==6.0.0b30",
    ],
    entry_points={
        "console_scripts": [
            "fwrap=fwrap.cli:main",
        ],
    },
    python_requires=">=3.8",
) 