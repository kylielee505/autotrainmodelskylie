import json
import os
import random
import string

import gradio as gr
import pandas as pd
from loguru import logger

from autotrain import help
from autotrain.dataset import AutoTrainDataset, AutoTrainDreamboothDataset, AutoTrainImageClassificationDataset
from autotrain.languages import SUPPORTED_LANGUAGES
from autotrain.params import Params
from autotrain.project import Project
from autotrain.tasks import COLUMN_MAPPING
from autotrain.utils import app_error_handler, get_project_cost, get_user_token, user_authentication


APP_TASKS = {
    "Natural Language Processing": ["Text Classification", "LLM Finetuning"],
    # "Tabular": TABULAR_TASKS,
    "Computer Vision": ["Image Classification", "Dreambooth"],
}

APP_TASKS_MAPPING = {
    "Text Classification": "text_multi_class_classification",
    "LLM Finetuning": "lm_training",
    "Image Classification": "image_multi_class_classification",
    "Dreambooth": "dreambooth",
}

APP_TASK_TYPE_MAPPING = {
    "text_classification": "Natural Language Processing",
    "lm_training": "Natural Language Processing",
    "image_classification": "Computer Vision",
    "dreambooth": "Computer Vision",
}


def _login_user(user_token):
    user_info = user_authentication(token=user_token)
    username = user_info["name"]

    user_can_pay = user_info["canPay"]
    orgs = user_info["orgs"]

    valid_orgs = [org for org in orgs if org["canPay"] is True]
    valid_orgs = [org for org in valid_orgs if org["roleInOrg"] in ("admin", "write")]
    valid_orgs = [org["name"] for org in valid_orgs]

    valid_can_pay = [username] + valid_orgs if user_can_pay else valid_orgs
    who_is_training = [username] + [org["name"] for org in orgs]
    return user_token, valid_can_pay, who_is_training


def _update_task_type(project_type):
    return gr.Dropdown.update(
        value=APP_TASKS[project_type][0],
        choices=APP_TASKS[project_type],
        visible=True,
    )


def _update_model_choice(task):
    # TODO: add tabular and remember, for tabular, we only support AutoTrain
    if task == "LLM Finetuning":
        model_choice = ["HuggingFace Hub"]
    else:
        model_choice = ["AutoTrain", "HuggingFace Hub"]

    return gr.Dropdown.update(
        value=model_choice[0],
        choices=model_choice,
        visible=True,
    )


def _update_file_upload(task):
    pass


def _update_file_type(task):
    task = APP_TASKS_MAPPING[task]
    if task in ("text_multi_class_classification", "lm_training"):
        return gr.Radio.update(
            value="CSV",
            choices=["CSV", "JSONL"],
            visible=True,
        )
    elif task == "image_multi_class_classification":
        return gr.Radio.update(
            value="Images",
            choices=["Image Subfolders", "ZIP"],
            visible=True,
        )
    elif task == "dreambooth":
        return gr.Radio.update(
            value="Images",
            choices=["Image Folder", "ZIP"],
            visible=True,
        )
    else:
        raise NotImplementedError


def _update_param_choice(model_choice):
    logger.info(f"model_choice: {model_choice}")
    choices = ["AutoTrain", "Manual"] if model_choice == "HuggingFace Hub" else ["AutoTrain"]
    return gr.Dropdown.update(
        value=choices[0],
        choices=choices,
        visible=True,
    )


def _project_type_update(project_type, task_type):
    logger.info(f"project_type: {project_type}, task_type: {task_type}")
    task_choices_update = _update_task_type(project_type)
    model_choices_update = _update_model_choice(task_choices_update["value"])
    return [
        task_choices_update,
        model_choices_update,
        _update_file_type(task_choices_update["value"]),
        _update_param_choice(model_choices_update["value"]),
    ]


def _task_type_update(task_type):
    logger.info(f"task_type: {task_type}")
    model_choices_update = _update_model_choice(task_type)
    return [model_choices_update, _update_file_type(task_type), _update_param_choice(model_choices_update["value"])]


def _estimate_costs(
    task, user_token, autotrain_username, project_name, training_data, validation_data, column_mapping, num_models
):
    task = APP_TASKS_MAPPING[task]
    # if task == "dreambooth":
    #     concept_images = [
    #         st.session_state.get(f"dreambooth_concept_images_{i + 1}") for i in range(number_of_concepts)
    #     ]
    #     if sum(len(x) for x in concept_images) == 0:
    #         raise ValueError("Please upload concept images")
    #     dset = AutoTrainDreamboothDataset(
    #         num_concepts=number_of_concepts,
    #         concept_images=[st.session_state[f"dreambooth_concept_images_{i + 1}"] for i in range(number_of_concepts)],
    #         concept_names=[st.session_state[f"dreambooth_concept_name_{i + 1}"] for i in range(number_of_concepts)],
    #         token=user_token,
    #         project_name=project_name,
    #         username=autotrain_username,
    #     )
    if task.startswith("image"):
        dset = AutoTrainImageClassificationDataset(
            train_data=training_data,
            token=user_token,
            project_name=project_name,
            username=autotrain_username,
            valid_data=validation_data,
            percent_valid=None,  # TODO: add to UI
        )
    else:
        dset = AutoTrainDataset(
            train_data=training_data,
            task=task,
            token=user_token,
            project_name=project_name,
            username=autotrain_username,
            column_mapping=column_mapping,
            valid_data=validation_data,
            percent_valid=None,  # TODO: add to UI
        )

    estimated_cost = get_project_cost(
        username=autotrain_username,
        token=user_token,
        task=task,
        num_samples=dset.num_samples,
        num_models=num_models,
    )
    return estimated_cost


def get_variable_name(var, namespace):
    for name in namespace:
        if namespace[name] is var:
            return name
    return None


def main():
    CSS = """
    .contain { display: flex; flex-direction: column; }
    #training_data_box { height: 100px; }
    """
    with gr.Blocks(css=CSS) as demo:
        training_params = gr.State([])
        gr.Markdown("## 🤗 AutoTrain Advanced")
        user_token = os.environ.get("HF_TOKEN", "")

        if len(user_token) == 0:
            user_token = get_user_token()

        if user_token is None:
            gr.Markdown(
                """Please login with a write [token](https://huggingface.co/settings/tokens).
                You can also pass your HF token in an environment variable called `HF_TOKEN` to avoid having to enter it every time.
                """
            )
            user_token_input = gr.Textbox(label="HuggingFace Token", value="", type="password", lines=1, max_lines=1)
            user_token = gr.Textbox(visible=False)
            valid_can_pay = gr.Textbox(visible=False)
            who_is_training = gr.Textbox(visible=False)
            user_token_input.submit(
                _login_user,
                inputs=[user_token_input],
                outputs=[user_token, valid_can_pay, who_is_training],
            )
            user_token = user_token.value
            valid_can_pay = valid_can_pay.value
            who_is_training = who_is_training.value
        else:
            user_token, valid_can_pay, who_is_training = _login_user(user_token)

        if user_token is None or len(user_token) == 0:
            gr.Error("Please login with a write token.")

        logger.info(who_is_training)
        with gr.Row():
            with gr.Column():
                autotrain_username = gr.Dropdown(
                    label="AutoTrain Username", choices=who_is_training, value=who_is_training[0]
                )
                random_project_name = "-".join(
                    ["".join(random.choices(string.ascii_lowercase + string.digits, k=4)) for _ in range(3)]
                )
                project_name = gr.Textbox(label="Project name", value=random_project_name, lines=1, max_lines=1)
                project_type = gr.Dropdown(
                    label="Project Type", choices=list(APP_TASKS.keys()), value=list(APP_TASKS.keys())[0]
                )
                task_type = gr.Dropdown(
                    label="Task",
                    choices=APP_TASKS[list(APP_TASKS.keys())[0]],
                    value=APP_TASKS[list(APP_TASKS.keys())[0]][0],
                    interactive=True,
                )
                with gr.Row():
                    model_choice = gr.Dropdown(
                        label="Model Choice",
                        choices=["AutoTrain", "HuggingFace Hub"],
                        value="AutoTrain",
                        visible=True,
                        interactive=True,
                    )
                    param_choice = gr.Dropdown(
                        label="Param Choice",
                        choices=["AutoTrain"],
                        value="AutoTrain",
                        visible=True,
                        interactive=True,
                    )
                process_data = gr.Button(value="Process Data")
            with gr.Column():
                hub_model = gr.Textbox(
                    label="Model",
                    value="",
                    visible=False,
                    interactive=True,
                    elem_id="hub_model",
                )
                with gr.Tabs():
                    with gr.TabItem("Params"):
                        with gr.Column():
                            with gr.Row():
                                source_language = gr.Dropdown(
                                    label="Source Language",
                                    choices=SUPPORTED_LANGUAGES[:-1],
                                    value="en",
                                    visible=True,
                                    interactive=True,
                                    elem_id="source_language",
                                )
                                target_language = gr.Dropdown(
                                    label="Target Language",
                                    choices=["fr"],
                                    value="fr",
                                    visible=False,
                                    interactive=True,
                                    elem_id="target_language",
                                )
                                image_size = gr.Number(
                                    label="Image Size",
                                    value=512,
                                    visible=False,
                                    interactive=True,
                                    elem_id="image_size",
                                )
                                num_models = gr.Slider(
                                    label="Number of Models",
                                    minimum=1,
                                    maximum=25,
                                    value=5,
                                    step=1,
                                    visible=True,
                                    interactive=True,
                                    elem_id="num_models",
                                )
                            with gr.Row():
                                learning_rate = gr.Number(
                                    label="Learning Rate",
                                    value=5e-5,
                                    visible=False,
                                    interactive=True,
                                    elem_id="learning_rate",
                                )
                                batch_size = gr.Number(
                                    label="Train Batch Size",
                                    value=32,
                                    visible=False,
                                    interactive=True,
                                    elem_id="train_batch_size",
                                )
                                num_epochs = gr.Number(
                                    label="Number of Epochs",
                                    value=3,
                                    visible=False,
                                    interactive=True,
                                    elem_id="num_train_epochs",
                                )
                            with gr.Row():
                                gradient_accumulation_steps = gr.Number(
                                    label="Gradient Accumulation Steps",
                                    value=1,
                                    visible=False,
                                    interactive=True,
                                    elem_id="gradient_accumulation_steps",
                                )
                                percentage_warmup_steps = gr.Number(
                                    label="Percentage of Warmup Steps",
                                    value=0.1,
                                    visible=False,
                                    interactive=True,
                                    elem_id="percentage_warmup",
                                )
                                weight_decay = gr.Number(
                                    label="Weight Decay",
                                    value=0.01,
                                    visible=False,
                                    interactive=True,
                                    elem_id="weight_decay",
                                )
                            with gr.Row():
                                lora_r = gr.Number(
                                    label="LoraR",
                                    value=16,
                                    visible=False,
                                    interactive=True,
                                    elem_id="lora_r",
                                )
                                lora_alpha = gr.Number(
                                    label="LoraAlpha",
                                    value=32,
                                    visible=False,
                                    interactive=True,
                                    elem_id="lora_alpha",
                                )
                                lora_dropout = gr.Number(
                                    label="Lora Dropout",
                                    value=0.1,
                                    visible=False,
                                    interactive=True,
                                    elem_id="lora_dropout",
                                )
                            with gr.Row():
                                db_num_steps = gr.Number(
                                    label="Num Steps",
                                    value=1000,
                                    visible=False,
                                    interactive=True,
                                    elem_id="num_steps",
                                )
                                db_prior_preservation = gr.Dropdown(
                                    label="Prior Preservation",
                                    choices=["True", "False"],
                                    value="True",
                                    visible=False,
                                    interactive=True,
                                    elem_id="prior_preservation",
                                )
                                db_text_encoder_steps_percentage = gr.Number(
                                    label="Text Encoder Steps Percentage",
                                    value=0.1,
                                    visible=False,
                                    interactive=True,
                                    elem_id="text_encoder_steps_percentage",
                                )
                            with gr.Row():
                                optimizer = gr.Dropdown(
                                    label="Optimizer",
                                    choices=["adamw_torch", "adamw_hf", "sgd", "adafactor", "adagrad"],
                                    value="adamw_torch",
                                    visible=False,
                                    interactive=True,
                                    elem_id="optimizer",
                                )
                                scheduler = gr.Dropdown(
                                    label="Scheduler",
                                    choices=["linear", "cosine"],
                                    value="linear",
                                    visible=False,
                                    interactive=True,
                                    elem_id="scheduler",
                                )

                        add_job_button = gr.Button(
                            value="Add Job",
                            visible=False,
                            interactive=True,
                            elem_id="add_job",
                        )

                    with gr.TabItem("Data"):
                        with gr.Column():
                            file_type_training = gr.Radio(
                                label="File Type",
                                choices=["CSV", "JSONL"],
                                value="CSV",
                                visible=True,
                                interactive=True,
                            )
                            training_data = gr.File(label="Training Data", elem_id="training_data_box")
                            # validation_data = gr.File(label="Validation Data (Optional)")
                            with gr.Row():
                                col_map_text = gr.Dropdown(
                                    label="Text Column", choices=[], visible=True, interactive=True
                                )
                                col_map_label = gr.Dropdown(
                                    label="Target Column", choices=[], visible=True, interactive=True
                                )

        hyperparameters = [
            hub_model,
            num_models,
            source_language,
            target_language,
            learning_rate,
            batch_size,
            num_epochs,
            gradient_accumulation_steps,
            lora_r,
            lora_alpha,
            lora_dropout,
            optimizer,
            scheduler,
            percentage_warmup_steps,
            weight_decay,
            db_num_steps,
            db_prior_preservation,
            image_size,
            db_text_encoder_steps_percentage,
        ]

        training_params_md = gr.DataFrame(visible=False, interactive=False)
        training_params_txt = gr.Text(value="[]", visible=False, interactive=False)

        def _update_params(params_data):
            logger.info(f"Updating params: {params_data}")
            _task = params_data[task_type]
            _task = APP_TASKS_MAPPING[_task]
            params = Params(
                task=_task,
                param_choice="autotrain" if params_data[param_choice] == "AutoTrain" else "manual",
                model_choice="autotrain" if params_data[model_choice] == "AutoTrain" else "hub_model",
            )
            params = params.get()
            logger.info(f"Params: {params}")
            visible_params = []
            for param in hyperparameters:
                # logger.info(getattr(param, list(params.keys())[0]))
                logger.info(f"Param: {param.elem_id}")
                if param.elem_id in params.keys():
                    visible_params.append(param.elem_id)
            op = [h.update(visible=h.elem_id in visible_params) for h in hyperparameters]
            if params_data[param_choice] == "AutoTrain":
                op.append(add_job_button.update(visible=False))
            else:
                op.append(add_job_button.update(visible=True))
            op.append(training_params_md.update(visible=False))
            op.append(training_params_txt.update(value="[]"))
            return op

        project_type.change(
            _project_type_update,
            inputs=[project_type, task_type],
            outputs=[task_type, model_choice, file_type_training, param_choice],
        )
        task_type.change(
            _task_type_update,
            inputs=[task_type],
            outputs=[model_choice, file_type_training, param_choice],
        )
        model_choice.change(
            _update_param_choice,
            inputs=model_choice,
            outputs=param_choice,
        )

        param_choice.change(
            _update_params,
            inputs=set([task_type, param_choice, model_choice] + hyperparameters + [add_job_button]),
            outputs=hyperparameters + [add_job_button, training_params_md, training_params_txt],
        )
        task_type.change(
            _update_params,
            inputs=set([task_type, param_choice, model_choice] + hyperparameters + [add_job_button]),
            outputs=hyperparameters + [add_job_button, training_params_md, training_params_txt],
        )
        model_choice.change(
            _update_params,
            inputs=set([task_type, param_choice, model_choice] + hyperparameters + [add_job_button]),
            outputs=hyperparameters + [add_job_button, training_params_md, training_params_txt],
        )

        def _add_job(params_data):
            _task = params_data[task_type]
            _task = APP_TASKS_MAPPING[_task]
            _param_choice = "autotrain" if params_data[param_choice] == "AutoTrain" else "manual"
            _model_choice = "autotrain" if params_data[model_choice] == "AutoTrain" else "hub_model"
            if _model_choice == "hub_model" and params_data[hub_model] is None:
                logger.error("Hub model is None")
                return
            _training_params = {}
            params = Params(task=_task, param_choice=_param_choice, model_choice=_model_choice)
            params = params.get()
            for _param in hyperparameters:
                print(f"Param: {_param.elem_id}, visible: {_param.visible}")
                if _param.elem_id in params.keys():
                    _training_params[_param.elem_id] = params_data[_param]
            # training_params.append(_training_params)
            logger.info(f"Training params: {_training_params}")
            # params_data[training_params].append(_training_params)
            logger.info(f"*** {repr(params_data[training_params_txt])}")
            _training_params_md = json.loads(params_data[training_params_txt])
            _training_params_md.append(_training_params)
            return [
                gr.DataFrame.update(value=pd.DataFrame(_training_params_md), visible=True),
                gr.Textbox.update(value=json.dumps(_training_params_md), visible=False),
            ]

        add_job_button.click(
            _add_job,
            inputs=set(
                [task_type, param_choice, model_choice] + hyperparameters + [training_params_md, training_params_txt]
            ),
            outputs=[training_params_md, training_params_txt],
        )

        # training_params.change(
        #     lambda x: training_params_md.update(f"Training Params: {x}"),
        #     inputs=training_params,
        #     outputs=training_params_md,
        # )

    return demo
