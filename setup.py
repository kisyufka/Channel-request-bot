from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="channel-request-bot",
    version="1.0.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="Telegram bot for processing channel join requests with age verification",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/channel-request-bot",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Communications :: Chat",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.8",
    install_requires=[
        "pyTelegramBotAPI>=4.21.1",
        "PyYAML>=6.0",
        "python-dotenv>=1.0.0",
    ],
    entry_points={
        "console_scripts": [
            "channel-request-bot=bot:main",
        ],
    },
    include_package_data=True,
    keywords="telegram bot channel request moderation age verification",
)
