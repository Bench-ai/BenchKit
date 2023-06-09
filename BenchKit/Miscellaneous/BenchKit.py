import json
import os
import tarfile
from pathlib import Path
import gzip
import shutil
import requests
from BenchKit.NeuralNetworks.Helpers import create_model_dir
from BenchKit.Train.Helpers import write_script
from .Settings import convert_timestamp
from .Verbose import verbose_logo, get_version
import argparse
import pandas as pd
from .User import get_user_project, get_dataset_list, get_versions, get_checkpoint_url, test_login, \
    list_all_checkpoints, delete_checkpoints, delete_dataset, delete_version, pull_project_code, kill_server
from tabulate import tabulate
from tqdm import tqdm
from BenchKit.Miscellaneous.MakeTar import extract_tar


def create_dataset():
    from BenchKit.Data.Helpers import create_dataset_dir
    create_dataset_dir()


def logout():
    cred_path = Path(__file__).resolve().parent / "credentials.json"
    if os.path.exists(cred_path):
        os.remove(cred_path)


def login_manual(project_id: str, api_key: str):
    cred_path = Path(__file__).resolve().parent / "credentials.json"

    with open(cred_path, "w") as file:
        json.dump({"project_id": project_id,
                   "api_key": api_key}, file)

    try:
        test_login()
    except RuntimeError:
        logout()
        raise ValueError("Credentials invalid")


def write_manager():
    template_path = Path(__file__).resolve().parent / "manage.txt"
    with open(template_path, "r") as f:
        with open("manage.py", "w") as file:
            line = f.readline()
            while line:
                file.write(line)
                line = f.readline()


def print_version():
    verbose_logo(get_version())


def load_project(project_id: str, api_key: str):
    login_manual(project_id, api_key)


def gracefully_stop_server():
    kill_server()


def show_versions():
    version_dict = get_versions()
    if not version_dict:
        raise ValueError("No versions have been uploaded")

    df = pd.DataFrame(data=version_dict)
    df = df.drop(columns=["project_id"])
    print(tabulate(df, headers='keys', tablefmt='psql', showindex=False))

    return df


def del_versions():
    show_versions()
    version_number = int(input("Enter the number of the version you wish to delete: "))
    delete_version(version_number)


def show_project():
    df = pd.DataFrame(data=[get_user_project()])
    df = df.drop(columns=["project_folder"])
    print(tabulate(df, headers='keys', tablefmt='psql'))


def show_checkpoints():
    checkpoint_dict = list_all_checkpoints()

    if not checkpoint_dict:
        raise ValueError("No checkpoints have been uploaded")

    df = pd.DataFrame(data=checkpoint_dict)

    df["creation_timestamp"] = df["creation_timestamp"].apply(convert_timestamp)
    df["update_timestamp"] = df["update_timestamp"].apply(convert_timestamp)

    id_col = df["id"].values

    df = df.drop(columns=['id'])

    print(tabulate(df, headers='keys', tablefmt='psql'))

    return df, id_col


def show_datasets():
    dataset_dict = get_dataset_list()

    if not dataset_dict:
        raise ValueError("No datasets have been uploaded")

    df = pd.DataFrame(data=dataset_dict)

    df["creation_timestamp"] = df["creation_timestamp"].apply(convert_timestamp)
    df["update_timestamp"] = df["update_timestamp"].apply(convert_timestamp)

    id_col = df["id"].values

    df = df.drop(columns=['id', 'project_id'])

    print(tabulate(df, headers='keys', tablefmt='psql'))

    return df, id_col


def del_datasets():
    _, id_col = show_datasets()
    dataset_number = int(input("Enter the number of the dataset you wish to delete: "))
    delete_dataset(id_col[dataset_number])


def del_checkpoint():
    _, id_col = show_checkpoints()
    checkpoint_number = int(input("Enter the number of the checkpoint you wish to delete: "))

    delete_checkpoints(id_col[checkpoint_number])


def get_checkpoint():
    checkpoint_df, id_col = show_checkpoints()
    checkpoint_number = int(input("Enter the number of the checkpoint you wish to pull: "))

    row = checkpoint_df.iloc[checkpoint_number]

    request = get_checkpoint_url(id_col[checkpoint_number])

    mem_zip = requests.get(request)

    with open(f"{row['checkpoint_name']}.tar.gz", 'wb') as f:
        f.write(mem_zip.content)

    with gzip.open(f"{row['checkpoint_name']}.tar.gz", 'rb') as f_in:
        with open(f"{row['checkpoint_name']}.tar", 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

    with tarfile.open(f"{row['checkpoint_name']}.tar", 'r') as tar:
        tar.extractall()

    os.remove(f"{row['checkpoint_name']}.tar.gz")
    os.remove(f"{row['checkpoint_name']}.tar")


def pull_version(version: int):
    code_dict = pull_project_code(version)

    for item in tqdm(code_dict.items()):
        k, v = item
        mem_zip = requests.get(v)
        with open(f"{k}.tar.gz", 'wb') as f:
            f.write(mem_zip.content)

        extract_tar(f"{k}.tar.gz", ".")
        os.remove(f"{k}.tar.gz")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("action",
                        choices=["start-project", "logout",
                                 "get-check", "del-check", "show-check",
                                 "show-ds", "del-ds", "project-info",
                                 "show-vs", "del-vs", "pull-vs",
                                 "stop-svr"],
                        nargs="?",
                        default=None)

    parser.add_argument("input_value",
                        nargs='?',
                        default=None)

    parser.add_argument("input_value1",
                        nargs='?',
                        default=None)

    parser.add_argument("input_value2",
                        nargs='?',
                        default=None)

    parser.add_argument("-v",
                        "--version",
                        action='store_true',
                        required=False)

    args = parser.parse_args()

    if args.version:
        print_version()

    if args.action == "logout":
        logout()

    if args.action == "get-check":
        get_checkpoint()

    if args.action == "del-check":
        del_checkpoint()

    if args.action == "show-check":
        show_checkpoints()

    if args.action == "show-ds":
        show_datasets()

    if args.action == "del-ds":
        del_datasets()

    if args.action == "project-info":
        show_project()

    if args.action == "del-vs":
        del_versions()

    if args.action == "show-vs":
        show_versions()

    if args.action == "stop-svr":
        gracefully_stop_server()

    if args.action == "pull-vs":

        if not args.input_value:
            raise ValueError("Project version was not provided")

        pull_version(args.input_value)

    if args.action == "start-project":

        if not args.input_value:
            raise ValueError("Project id was not provided")

        if not args.input_value1:
            raise ValueError("Apikey was not provided")

        load_project(args.input_value, args.input_value1)
        write_manager()
        create_dataset()
        create_model_dir()
        write_script()


if __name__ == '__main__':
    main()
