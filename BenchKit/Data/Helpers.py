import json
import os
import requests
from colorama import Fore, Style
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import torch
from torch.utils.data import DataLoader
import shutil
import pathlib
from tqdm import tqdm
from BenchKit.Data.Datasets import ProcessorDataset
from BenchKit.Miscellaneous.User import create_dataset, get_post_url, patch_dataset_list, delete_dataset, \
    get_current_dataset

megabyte = 1_024 ** 2
gigabyte = megabyte * 1024
terabyte = gigabyte * 1024
limit = 100 * megabyte


class UploadError(Exception):
    pass


def get_dataset(chunk_class,
                cloud: bool,
                dataset_name: str,
                batch_size: int,
                num_workers: int,
                *args,
                **kwargs):

    dl = DataLoader(dataset=chunk_class(dataset_name,
                                        cloud,
                                        *args,
                                        **kwargs),
                    num_workers=num_workers,
                    batch_size=batch_size,
                    worker_init_fn=chunk_class.worker_init_fn)

    return dl


def remove_all_temps():
    for i in os.listdir("."):
        if i.startswith("Temp"):
            shutil.rmtree(os.path.join(".", i))


def upload_file(url,
                file_path,
                save_path,
                fields):

    with open(file_path, 'rb') as f:
        files = {'file': (save_path, f)}
        http_response = requests.post(url,
                                      data=fields,
                                      files=files)

    if http_response.status_code != 204:
        raise RuntimeError(f"Failed to Upload {file_path}")


def create_dataset_zips(processed_dataset: ProcessorDataset,
                        dataset_name: str):

    ds = get_current_dataset(dataset_name)

    if ds:
        delete_dataset(ds["id"])

    create_dataset(dataset_name)
    ds = get_current_dataset(dataset_name)

    if os.path.isdir(f"ProjectDatasets/{dataset_name}"):
        shutil.rmtree(f"ProjectDatasets/{dataset_name}")

    is_2 = False
    for i in DataLoader(processed_dataset, batch_size=1):
        if len(i) == 2:
            is_2 = True
        break

    print(Fore.RED + "Started Data processing" + Style.RESET_ALL)

    if is_2:
        count = save_file_and_label(processed_dataset, dataset_name)
    else:
        count = save_label_data(processed_dataset, dataset_name)

    affirm_size(f"ProjectDatasets/{dataset_name}")

    patch_dataset_list(ds["id"], count)

    print(Fore.GREEN + "Data is processed" + Style.RESET_ALL)


def test_dataloading(dataset_name: str,
                     chunk_dataset,
                     *args,
                     **kwargs):
    num_workers = 2
    batch_size = 16

    ds = get_current_dataset(dataset_name)

    if not ds:
        raise RuntimeError("Dataset must be created")

    length = ds["sample_count"]

    if length == 0:
        raise RuntimeError("Data has not been processed")

    dl = get_dataset(chunk_dataset,
                     False,
                     ds["name"],
                     batch_size,
                     num_workers,
                     *args,
                     **kwargs)

    print(Fore.RED + "Running Data Loading test" + Style.RESET_ALL)
    for _ in tqdm(dl, colour="blue", total=int(np.ceil(length / batch_size)) + 1):
        pass

    remove_all_temps()

    print(Fore.GREEN + "Data Loading Test Passed" + Style.RESET_ALL)


def run_upload(dataset_name: str):
    ds = get_current_dataset(dataset_name)

    if not ds:
        raise RuntimeError("Dataset must be created")

    length = ds["sample_count"]

    if os.path.isdir(f"ProjectDatasets/{dataset_name}"):
        x = os.listdir(f"ProjectDatasets/{dataset_name}")
        if len(x) == 0:
            raise RuntimeError("Project Folder is empty")
    else:
        raise RuntimeError("Project Folder does not exist")

    if length == 0:
        raise RuntimeError("Data has not been processed")

    print(Fore.RED + "Started Upload" + Style.RESET_ALL)

    save_path = f"ProjectDatasets/{dataset_name}"

    for path in tqdm(iterate_directory(save_path, ds["last_file_number"]),
                     total=(len(os.listdir(save_path)) - ds["last_file_number"]),
                     colour="blue"):

        response = get_post_url(ds["id"],
                                os.path.getsize(path),
                                os.path.split(path)[-1])

        resp = json.loads(response.content)
        upload_file(resp["url"],
                    path,
                    os.path.split(path)[-1],
                    resp["fields"])

        ds = get_current_dataset(dataset_name)

    print(Fore.GREEN + "Finished Upload" + Style.RESET_ALL)

    shutil.rmtree(f"ProjectDatasets/{dataset_name}")


def copy_file(folder_path: str,
              file_folder: str,
              chunk_num: int,
              idx: int,
              files: list):
    os.makedirs(os.path.join(folder_path, file_folder.format(chunk_num), f"files-{idx}"))
    for inner_idx, j in enumerate(files):
        shutil.copyfile(j, os.path.join(folder_path,
                                        file_folder.format(chunk_num),
                                        f"files-{idx}",
                                        os.path.split(j)[-1]))


def save_folder_data(save_folder: str,
                     chunk_num: int,
                     label_batch: list,
                     file_batch: list,
                     data_length: int):
    file_str = "dataset-labels-{}.pt"
    folder_str = "dataset-chunk-{}"
    zip_str = f"dataset-{chunk_num}-{data_length}-zip"
    file_folder = "dataset-files-folder-{}"

    folder_path = os.path.join(save_folder, folder_str.format(chunk_num))
    os.makedirs(folder_path)
    torch.save(label_batch, os.path.join(folder_path, file_str.format(chunk_num)))

    os.makedirs(os.path.join(folder_path, file_folder.format(chunk_num)))

    with ThreadPoolExecutor(15) as exe:
        _ = [exe.submit(copy_file,
                        folder_path,
                        file_folder,
                        chunk_num,
                        idx,
                        i) for idx, i in enumerate(file_batch)]

    shutil.make_archive(os.path.join(save_folder, zip_str),
                        "zip",
                        folder_path)

    shutil.rmtree(folder_path)


def save_file_and_label(dataset: ProcessorDataset,
                        ds_name: str):
    cwd = os.getcwd()
    save_folder = os.path.join(cwd, "ProjectDatasets", ds_name)

    if os.path.isdir(save_folder):
        raise UploadError("Folder already exists")
    else:
        os.makedirs(save_folder)

    dataloader = DataLoader(dataset=dataset,
                            shuffle=True,
                            num_workers=4,
                            batch_size=1)
    chunk_num = 0

    label_batch = []
    file_batch = []
    current_file_size = 0

    count = 0

    for batch in tqdm(dataloader,
                      colour="blue"):
        count += 1
        labels, file = batch

        if current_file_size <= limit:
            current_file_size += int(np.sum([os.path.getsize(i) for i in file[0]]))
            file_batch.append(file[0])
            label_batch.append(labels)

        else:
            save_folder_data(save_folder,
                             chunk_num,
                             label_batch,
                             file_batch,
                             len(file_batch))

            file_batch = []
            label_batch = []
            current_file_size = 0
            current_file_size += int(np.sum([os.path.getsize(i) for i in file[0]]))
            file_batch.append(file[0])
            label_batch.append(labels)
            chunk_num += 1

    save_folder_data(save_folder,
                     chunk_num,
                     label_batch,
                     file_batch,
                     len(file_batch))
    return count


def save_label_data(dataset: ProcessorDataset,
                    ds_name: str):
    # needs to be seriously fixed
    # from BenchKit.Miscellaneous.Settings import get_config
    cwd = os.getcwd()
    save_folder = os.path.join(cwd, "ProjectDatasets", ds_name)
    # config = get_config()

    # ds_list: list = config["datasets"]
    # ds_list.append({
    #     "name": ds_name,
    #     "path": save_folder
    # })

    if os.path.isdir(save_folder):
        raise UploadError("Folder already exists")
    else:
        os.makedirs(save_folder)

    dataloader = DataLoader(dataset=dataset,
                            shuffle=True,
                            num_workers=4,
                            batch_size=1)

    arr_len = None
    arr = []

    chunk_num = 0
    folder_name = "dataset-chunk-{}"
    file_str = "dataset-labels-{}.pt"
    zip_str = "dataset-{}-{}-zip"

    folder_path = os.path.join(save_folder, folder_name.format(chunk_num))

    for batch in tqdm(dataloader, colour="blue"):

        if os.path.isdir(folder_path):
            arr.append(batch)

            if len(arr) - 1 == arr_len:
                torch.save(arr, os.path.join(folder_path, file_str.format(chunk_num)))
                chunk_num += 1
                folder_path = os.path.join(save_folder, folder_name.format(chunk_num))
                arr = []
        else:
            os.mkdir(folder_path)
            file_path = os.path.join(folder_path, file_str.format(chunk_num))
            torch.save([batch], file_path)
            f_size = os.path.getsize(file_path)
            arr_len = np.ceil(limit / f_size)
            arr.append(batch)
            os.remove(file_path)

    torch.save(arr, os.path.join(folder_path, file_str.format(chunk_num)))

    shutil.make_archive(os.path.join(save_folder, zip_str.format(chunk_num, len(arr))),
                        "zip",
                        folder_path)

    shutil.rmtree(folder_path)

    # return ds_list


def get_dir_size(path) -> int:
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    return total


def merge_folders(small_folder: str, large_folder: str, save_folder: str):
    small_path = os.path.join(save_folder, os.path.split(small_folder)[-1].split(".")[0])
    large_path = os.path.join(save_folder, os.path.split(large_folder)[-1].split(".")[0])

    shutil.unpack_archive(small_folder, small_path)
    shutil.unpack_archive(large_folder, large_path)

    small_tensor = []
    large_tensor = []

    small_files = []
    large_files = []

    lt_path = ''
    lf_path = ''

    for i in os.listdir(small_path):
        tail = small_path.split(".")[0]
        pth = os.path.join(tail, i)
        if i.endswith(".pt"):
            small_tensor: list = torch.load(pth)
        else:
            small_files = [os.path.join(pth, i) for i in os.listdir(pth)]

    for i in os.listdir(large_path):
        tail = large_path.split(".")[0]
        pth = os.path.join(tail, i)
        if i.endswith(".pt"):
            lt_path = pth
            large_tensor: list = torch.load(pth)
        else:
            lf_path = pth
            large_files = [os.path.join(pth, i) for i in os.listdir(pth)]

    large_tensor.extend(small_tensor)

    if large_files:
        large_files = sorted(large_files, key=lambda x: int(x.split("-")[-1]))
        small_files = sorted(small_files, key=lambda x: int(x.split("-")[-1]))

        last_int = int(large_files[-1].split("-")[-1])

        with ThreadPoolExecutor(15) as exe:
            _ = [exe.submit(shutil.copytree,
                            i,
                            os.path.join(lf_path, f"file-{idx + last_int}")) for idx, i in
                 enumerate(small_files)]

    torch.save(large_tensor, lt_path)
    os.remove(small_folder)
    os.remove(large_folder)
    shutil.rmtree(small_path)

    head, tail = os.path.split(large_folder)

    f_name = tail.split(".")[0]

    f_list = f_name.split("-")
    f_name = f"dataset-{f_list[1]}-{len(large_tensor)}-zip"
    shutil.make_archive(f"{head}/{f_name}",
                        "zip",
                        large_path)

    shutil.rmtree(large_path)

    return large_folder


# test in the morning
def affirm_size(save_folder: str):
    pass_size_requirement = []
    fails_size_requirement = []

    if get_dir_size(save_folder) < megabyte * 100:
        raise RuntimeError("Dataset must be greater than 100 megabytes")

    for i in os.listdir(save_folder):
        path: str = os.path.join(save_folder, i)

        if os.path.getsize(path) >= limit:
            pass_size_requirement += [path]
        else:
            fails_size_requirement += [path]

    while len(fails_size_requirement) != 1:
        large_folder = fails_size_requirement[0]
        small_folder = fails_size_requirement.pop()

        large_folder = merge_folders(small_folder, large_folder, save_folder)

        size = os.path.getsize(large_folder)

        if size >= limit:
            pass_size_requirement.append(fails_size_requirement.pop(0))

    if len(pass_size_requirement) > 0:

        while len(fails_size_requirement) > 0:
            small_folder = fails_size_requirement.pop()
            large_folder = pass_size_requirement.pop()

            large_folder = merge_folders(small_folder, large_folder, save_folder)

            pass_size_requirement.insert(0, large_folder)


def iterate_directory(file_dir: str,
                      current_file: int) -> tuple[str, bool]:
    for idx, i in enumerate(os.listdir(file_dir)):
        if idx >= current_file:
            yield str(pathlib.Path(file_dir).resolve() / i)


def create_dataset_dir():
    if os.path.isdir("./Datasets"):
        pass
    else:
        current_path = "./Datasets"
        os.mkdir(current_path)

        whole_path = os.path.join(current_path, "ProjectDatasets.py")
        init_path = os.path.join(current_path, "__init__.py")

        with open(init_path, "w"):
            pass

        with open(whole_path, "w") as file:
            file.write("from BenchKit.Data.Datasets import ProcessorDataset, IterableChunk\n")
            file.write("from BenchKit.Data.Helpers import process_datasets\n")
            file.write("# Write your datasets or datapipes here")
            file.write("\n")
            file.write("\n")
            file.write("\n")
            file.write("\n")
            file.write("def main():\n")
            file.write('    """\n')
            file.write("    This method returns all the necessary components to build your dataset\n")
            file.write("    You will return a list of tuples, each tuple represents a different dataset\n")
            file.write("    The elements of the tuple represent the components to construct your dataset\n")
            file.write("    Element one will be your ProcessorDataset\n")
            file.write("    Element two will be your IterableChunk\n")
            file.write("    Element three will be the name of your Dataset\n")
            file.write("    Element four will be all the args needed for your Iterable Chunk as a list\n")
            file.write("    Element five will be all the kwargs needed for your Iterable Chunk as a Dict\n")
            file.write('    """\n')
            file.write("    pass\n")
