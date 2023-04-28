"""
    Author: Heli Qi
    Affiliation: NAIST
    Date: 2023.02
"""
import argparse
import os
from typing import Dict, List

import textgrid as tg
import numpy as np

from multiprocessing import Pool
from functools import partial
from tqdm import tqdm
from collections import Counter

from speechain.utilbox.import_util import parse_path_args
from speechain.utilbox.data_loading_util import load_idx2data_file, search_file_in_subfolder
from speechain.utilbox.type_util import str2bool, str2none


def parse():
    parser = argparse.ArgumentParser(description='params')
    parser.add_argument('--data_path', type=str, required=True,
                        help="The path where you place the dumped data.")
    parser.add_argument('--save_path', type=str, default=None,
                        help="The path where you want to save the duration metadata files. If not given, the files will be saved to {data_path}/mfa. (default: None)")
    parser.add_argument('--pretrained_model_name', type=str, required=True,
                        help="The name of the pretrained model you have used to get the .TextGrid files.")
    parser.add_argument('--retain_stress', type=str2bool, default=True,
                        help="Whether to retain the stress indicators at the end of each vowel phonemes. (default: True)")
    parser.add_argument('--dataset_name', type=str, required=True,
                        help="The name of the dataset you want to process.")
    parser.add_argument('--subset_name', type=str2none, default=None,
                        help="The name of the subset in your given dataset you want to process. (default: None)")
    parser.add_argument("--ncpu", type=int, default=8,
                        help="The number of processes you want to use to calculate the phoneme duration. (default: 8)")
    return parser.parse_args()


def dump_subset_metadata(dataset_path: str, save_path: str, subset_name: str, retain_stress: bool,
                         idx2text: Dict, idx2duration: Dict):
    """
        Save the subset-specific idx2text and idx2duration dictionaries as metadata files.

        Args:
            dataset_path (str):
                The path to the dataset directory.
            save_path (str):
                The path to save the metadata files.
            subset_name (str):
                The name of the subset to process.
            retain_stress (bool):
                Whether to retain the stress indicators at the end of each vowel phonemes.
            idx2text (Dict):
                The dictionary mapping file indices to tokenized text.
            idx2duration (Dict):
                The dictionary mapping file indices to phoneme durations.
    """


    # get the subset-specific idx2text and idx2duration Dicts
    subset_indices = list(load_idx2data_file(os.path.join(dataset_path, subset_name, 'idx2wav')).keys())
    subset_idx2text = {index: idx2text[index] for index in subset_indices if index in idx2text.keys()}
    subset_idx2duration = {index: idx2duration[index] for index in subset_indices if index in idx2duration.keys()}
    subset_path = os.path.join(save_path, subset_name, 'stress' if retain_stress else 'no-stress')

    # --- idx2text & idx2text_len Saving --- #
    os.makedirs(subset_path, exist_ok=True)
    # save the subet-specific idx2text Dict to a metadata file
    text_path = os.path.join(subset_path, 'idx2text')
    np.savetxt(text_path, [[idx, str(text)] for idx, text in subset_idx2text.items()], fmt="%s")
    print(f"Tokenized text has been successfully saved to {text_path}.")

    # save the length information of the subet-specific idx2text Dict to a metadata file
    text_len_path = os.path.join(subset_path, 'idx2text_len')
    np.savetxt(text_len_path, [[idx, len(text)] for idx, text in subset_idx2text.items()], fmt="%s")
    print(f"The length of tokenized text has been successfully saved to {text_len_path}.")

    # --- vocab Saving --- #
    subset_phns = []
    for text in subset_idx2text.values():
        subset_phns += text
    # collect the occurrence frequency of each phoneme
    phn2freq = sorted(Counter(subset_phns).items(), key=lambda x: x[1], reverse=True)
    subset_phns = [phn for phn, _ in phn2freq]
    if '<unk>' in subset_phns:
        subset_phns.remove('<unk>')
    # <sos/eos> is added here for the compatibility with autoregressive TTS model
    subset_phn_vocab = ["<blank>"] + subset_phns + ['<unk>', '<sos/eos>']

    vocab_path = os.path.join(subset_path, 'vocab')
    np.savetxt(vocab_path, subset_phn_vocab, fmt="%s")
    print(f"Phoneme vocabulary has been successfully saved to {vocab_path}.")

    # --- idx2duration Saving --- #
    os.makedirs(subset_path, exist_ok=True)
    # save the subet-specific idx2duration Dict to a metadata file
    duration_path = os.path.join(subset_path, 'idx2duration')
    np.savetxt(duration_path, [[idx, str(duration)] for idx, duration in subset_idx2duration.items()], fmt="%s")
    print(f"The duration of tokenized text has been successfully saved to {duration_path}.")

def cal_duration_by_tg(tg_file_list: List[str], retain_stress: bool):
    """
        Calculate the phoneme duration for each TextGrid file and return idx2text and idx2duration dictionaries.

        Args:
            tg_file_list (List[str]):
                List of paths to TextGrid files.
            retain_stress (bool):
                Whether to retain the stress indicators at the end of each vowel phonemes.

        Returns:
            Tuple[Dict, Dict]:
                A tuple containing two dictionaries:
                    1. idx2text: mapping file indices to tokenized text
                    2. idx2duration: mapping file indices to phoneme durations
    """
    idx2text, idx2duration = {}, {}
    for tg_file in tqdm(tg_file_list):
        file_name = os.path.basename(tg_file).split('.')[0]
        idx2text[file_name], idx2duration[file_name] = [], []

        text_grid = tg.TextGrid.fromFile(tg_file)
        word_boundary_dict = {round(word.maxTime, 2): True if word.mark not in ['sp', '', 'sil'] else False
                              for word in text_grid.tiers[0].intervals}
        for phn in text_grid.tiers[1].intervals:
            # ensure the accuracy of the float numbers for data storage
            phn_duration, phn_boundary = round(phn.maxTime - phn.minTime, 2), round(phn.maxTime, 2)

            # for the non-silence phoneme tokens, repeating of non-silence phonemes is retained
            if phn.mark not in ['sp', '', 'sil']:
                # remove the stress number at the end if retain_stress is set to False
                if not retain_stress and phn.mark[-1].isdigit():
                    phn_token = phn.mark[:-1]
                else:
                    phn_token = phn.mark

                idx2text[file_name].append(phn_token if phn_token != 'spn' else '<unk>')
                idx2duration[file_name].append(phn_duration)
            # for the silence tokens
            else:
                # when encounter a new space token, record it
                if len(idx2text[file_name]) == 0 or idx2text[file_name][-1] != '<space>':
                    idx2text[file_name].append('<space>')
                    idx2duration[file_name].append(phn_duration)
                # for the repeated space tokens, combine them together
                else:
                    idx2duration[file_name][-1] += phn_duration

            if phn_boundary in word_boundary_dict.keys() and word_boundary_dict[phn_boundary]:
                idx2text[file_name].append('<space>')
                idx2duration[file_name].append(round(0, 2))

        # remove the utterances that only contains a space token
        if len(idx2text[file_name]) == 1 and idx2text[file_name][0] == '<space>':
            idx2text.pop(file_name)
            idx2duration.pop(file_name)

    return idx2text, idx2duration

def main(data_path: str, pretrained_model_name: str, retain_stress: bool, dataset_name: str, subset_name: str = None,
         save_path: str = None, ncpu: int = 8):
    """
        Main function to process TextGrid files and save metadata files for a given dataset and subset.

        Args:
            data_path (str):
                The path where you placed the dumped data.
            pretrained_model_name (str):
                The name of the pretrained model you have used to get the .TextGrid files.
            retain_stress: bool
                Whether to retain the stress indicators at the end of each vowel phonemes.
            dataset_name (str):
                The name of the dataset you want to process.
            subset_name (str):
                The name of the subset in your given dataset you want to process.
            save_path (str, optional):
                The path where you want to save the duration metadata files.
                If not given, the files will be saved to {data_path}/mfa. Defaults to None.
            ncpu (int, optional):
                The number of processes you want to use to calculate the phoneme duration. Defaults to 8.
    """
    assert dataset_name in ['ljspeech', 'libritts', 'librispeech'], \
        f"Unknown dataset: {dataset_name}! It must be one of ['ljspeech', 'libritts', 'librispeech']."

    data_path = parse_path_args(data_path)
    if save_path is None:
        save_path = os.path.join(data_path, 'mfa', pretrained_model_name)

    # initialize the textgrid information and calculate the duration (in seconds)
    textgrid_path = os.path.join(data_path, 'mfa', pretrained_model_name, 'TextGrid')
    if subset_name is not None:
        textgrid_path = os.path.join(textgrid_path, subset_name)
    print(f'Start to summarize all the .TextGrid files in {textgrid_path}......')
    tg_file_list = search_file_in_subfolder(textgrid_path, tgt_match_fn=lambda x: x.endswith('.TextGrid'))
    if len(tg_file_list) == 0:
        raise RuntimeError(f".TextGrid files have not been successfully saved to {textgrid_path}!")
    func_args = [tg_file_list[i::ncpu] for i in range(ncpu)]
    cal_duration_by_tg_func = partial(cal_duration_by_tg, retain_stress=retain_stress)

    # start the executing jobs
    with Pool(ncpu) as executor:
        text_duration_results = executor.map(cal_duration_by_tg_func, func_args)

    # gather the results from all the processes
    idx2text, idx2duration = {}, {}
    for _idx2text, _idx2duration in text_duration_results:
        idx2text.update(_idx2text)
        idx2duration.update(_idx2duration)

    # dump the vocabulary and duration metadata files
    dataset_path = os.path.join(data_path, 'wav')
    # process all the subsets in the given dataset
    if subset_name is None:
        for file_name in os.listdir(textgrid_path):
            dump_subset_metadata(dataset_path=dataset_path, save_path=save_path, subset_name=file_name,
                                 retain_stress=retain_stress, idx2text=idx2text, idx2duration=idx2duration)
    else:
        dump_subset_metadata(dataset_path=dataset_path, save_path=save_path, subset_name=subset_name,
                             retain_stress=retain_stress, idx2text=idx2text, idx2duration=idx2duration)


if __name__ == '__main__':
    args = parse()
    main(**vars(args))

    # main(
    #     data_path="datasets/libritts/data",
    #     pretrained_model_name="librispeech_train-clean-100",
    #     dataset_name='libritts',
    #     subset_name=None,
    #     retain_stress=False,
    #     ncpu=8
    # )
