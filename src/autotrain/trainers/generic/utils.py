import os
import subprocess

import requests
from huggingface_hub import HfApi, snapshot_download
from loguru import logger


def create_dataset_repo(username, project_name, script_path, token):
    logger.info("Creating dataset repo...")
    api = HfApi(token=token)
    repo_id = f"{username}/autotrain-{project_name}"
    api.create_repo(
        repo_id=repo_id,
        repo_type="dataset",
        private=True,
    )
    logger.info("Uploading dataset...")
    api.upload_folder(
        folder_path=script_path,
        repo_id=repo_id,
        repo_type="dataset",
    )
    logger.info("Dataset uploaded.")
    return repo_id


def pull_dataset_repo(params):
    snapshot_download(
        repo_id=params.data_path,
        local_dir=params.project_name,
        token=params.token,
        repo_type="dataset",
    )


def install_requirements(params):
    # check if params.project_name has a requirements.txt
    if os.path.exists(f"{params.project_name}/requirements.txt"):
        # install the requirements using subprocess, wait for it to finish
        pipe = subprocess.Popen(
            [
                "pip",
                "install",
                "-r",
                "requirements.txt",
            ],
            cwd=params.project_name,
        )
        pipe.wait()
        logger.info("Requirements installed.")
        return
    logger.info("No requirements.txt found. Skipping requirements installation.")
    return


def run_command(params):
    if os.path.exists(f"{params.project_name}/script.py"):
        cmd = ["python", "script.py"]
        pipe = subprocess.Popen(cmd, cwd=params.project_name)
        pipe.wait()
        logger.info("Command finished.")
        return
    raise ValueError("No script.py found.")


def pause_endpoint(params):
    endpoint_id = os.environ["ENDPOINT_ID"]
    username = endpoint_id.split("/")[0]
    project_name = endpoint_id.split("/")[1]
    api_url = f"https://api.endpoints.huggingface.cloud/v2/endpoint/{username}/{project_name}/pause"
    headers = {"Authorization": f"Bearer {params.token}"}
    r = requests.post(api_url, headers=headers)
    return r.json()
