import io
import json
import os
from dataclasses import dataclass
from typing import Union

import requests
from huggingface_hub import HfApi

from autotrain import logger
from autotrain.dataset import AutoTrainDataset
from autotrain.trainers.clm.params import LLMTrainingParams
from autotrain.trainers.image_classification.params import ImageClassificationParams
from autotrain.trainers.text_classification.params import TextClassificationParams


def _llm_munge_data(params, username):
    train_data_path = f"{params.data_path}/{params.train_split}.csv"
    if params.valid_split is not None:
        valid_data_path = f"{params.data_path}/{params.valid_split}.csv"
    else:
        valid_data_path = []
    if os.path.exists(train_data_path):
        dset = AutoTrainDataset(
            train_data=[train_data_path],
            task="lm_training",
            token=params.token,
            project_name=params.project_name,
            username=username,
            column_mapping={"text": params.text_column},
            valid_data=valid_data_path,
            percent_valid=None,  # TODO: add to UI
        )
        dset.prepare()
        return f"{username}/autotrain-data-{params.project_name}"

    return params.data_path


@dataclass
class EndpointsRunner:
    params: Union[TextClassificationParams, ImageClassificationParams, LLMTrainingParams]
    backend: str

    def __post_init__(self):
        self.endpoints_backends = {
            "ep-aws-useast1-s": "aws_us-east-1_gpu_small_g4dn.xlarge",
            "ep-aws-useast1-m": "aws_us-east-1_gpu_medium_g5.2xlarge",
            "ep-aws-useast1-l": "aws_us-east-1_gpu_large_g4dn.12xlarge",
            "ep-aws-useast1-xl": "aws_us-east-1_gpu_xlarge_p4de",
            "ep-aws-useast1-2xl": "aws_us-east-1_gpu_2xlarge_p4de",
            "ep-aws-useast1-4xl": "aws_us-east-1_gpu_4xlarge_p4de",
            "ep-aws-useast1-8xl": "aws_us-east-1_gpu_8xlarge_p4de",
        }
        self.username = self.params.repo_id.split("/")[0]
        self.api_url = f"https://api.endpoints.huggingface.cloud/v2/endpoint/{self.username}"
        if isinstance(self.params, LLMTrainingParams):
            self.task_id = 9

    def _create_endpoint(self):
        hardware = self.endpoints_backends[self.backend]
        accelerator = hardware.split("_")[2]
        instance_size = hardware.split("_")[3]
        region = hardware.split("_")[1]
        vendor = hardware.split("_")[0]
        instance_type = hardware.split("_")[4]
        payload = {
            "accountId": self.username,
            "compute": {
                "accelerator": accelerator,
                "instanceSize": instance_size,
                "instanceType": instance_type,
                "scaling": {"maxReplica": 1, "minReplica": 1},
            },
            "model": {
                "framework": "custom",
                "image": {
                    "custom": {
                        "env": {
                            "HF_TOKEN": self.params.token,
                            "AUTOTRAIN_USERNAME": self.username,
                            "PROJECT_NAME": self.params.project_name,
                            "PARAMS": json.dumps(self.params.json()),
                            "DATA_PATH": self.params.data_path,
                            "TASK_ID": str(self.task_id),
                            "MODEL": self.params.model,
                            "OUTPUT_MODEL_REPO": self.params.repo_id,
                            "ENDPOINT_ID": f"{self.username}/{self.params.project_name}",
                        },
                        "health_route": "/",
                        "port": 7860,
                        "url": "huggingface/autotrain-advanced-api:latest",
                    }
                },
                "repository": "autotrain-projects/autotrain-advanced",
                "revision": "main",
                "task": "custom",
            },
            "name": self.params.project_name,
            "provider": {"region": region, "vendor": vendor},
            "type": "protected",
        }
        headers = {"Authorization": f"Bearer {self.params.token}"}
        r = requests.post(self.api_url, json=payload, headers=headers)
        logger.info(r.json())
        return r.json()

    def prepare(self):
        if isinstance(self.params, LLMTrainingParams):
            data_path = _llm_munge_data(self.params, self.username)
            self.params.data_path = data_path
            endpoint_id = self._create_endpoint()
            return endpoint_id
        raise NotImplementedError


@dataclass
class SpaceRunner:
    params: Union[TextClassificationParams, ImageClassificationParams, LLMTrainingParams]
    backend: str

    def __post_init__(self):
        self.spaces_backends = {
            "a10gl": "a10g-large",
            "a10gs": "a10g-small",
            "a100": "a100-large",
            "t4m": "t4-medium",
            "t4s": "t4-small",
        }
        self.username = self.params.repo_id.split("/")[0]
        if isinstance(self.params, LLMTrainingParams):
            self.task_id = 9

    def prepare(self):
        if isinstance(self.params, LLMTrainingParams):
            self.task_id = 9
            data_path = _llm_munge_data(self.params, self.username)
            self.params.data_path = data_path
            space_id = self._create_space()
            return space_id
        raise NotImplementedError

    def _create_readme(self):
        _readme = "---\n"
        _readme += f"title: {self.params.project_name}\n"
        _readme += "emoji: 🚀\n"
        _readme += "colorFrom: green\n"
        _readme += "colorTo: indigo\n"
        _readme += "sdk: docker\n"
        _readme += "pinned: false\n"
        _readme += "duplicated_from: autotrain-projects/autotrain-advanced\n"
        _readme += "---\n"
        _readme = io.BytesIO(_readme.encode())
        return _readme

    def _add_secrets(self, api, repo_id):
        api.add_space_secret(repo_id=repo_id, key="HF_TOKEN", value=self.params.token)
        api.add_space_secret(repo_id=repo_id, key="AUTOTRAIN_USERNAME", value=self.username)
        api.add_space_secret(repo_id=repo_id, key="PROJECT_NAME", value=self.params.project_name)
        api.add_space_secret(repo_id=repo_id, key="PARAMS", value=json.dumps(self.params.json()))
        api.add_space_secret(repo_id=repo_id, key="DATA_PATH", value=self.params.data_path)
        api.add_space_secret(repo_id=repo_id, key="TASK_ID", value=str(self.task_id))
        api.add_space_secret(repo_id=repo_id, key="MODEL", value=self.params.model)
        api.add_space_secret(repo_id=repo_id, key="OUTPUT_MODEL_REPO", value=self.params.repo_id)

    def _create_space(self):
        api = HfApi(token=self.params.token)
        repo_id = f"{self.username}/autotrain-{self.params.project_name}"
        api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            space_sdk="docker",
            space_hardware=self.spaces_backends[self.backend.split("-")[1].lower()],
            private=True,
        )
        self._add_secrets(api, repo_id)
        readme = self._create_readme()
        api.upload_file(
            path_or_fileobj=readme,
            path_in_repo="README.md",
            repo_id=repo_id,
            repo_type="space",
        )

        _dockerfile = "FROM huggingface/autotrain-advanced:latest\nCMD autotrain api --port 7860 --host 0.0.0.0"
        _dockerfile = io.BytesIO(_dockerfile.encode())
        api.upload_file(
            path_or_fileobj=_dockerfile,
            path_in_repo="Dockerfile",
            repo_id=repo_id,
            repo_type="space",
        )
        return repo_id
