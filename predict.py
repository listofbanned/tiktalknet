# Prediction interface for Cog ⚙️
# https://github.com/replicate/cog/blob/main/docs/python.md

from cog import BasePredictor, BaseModel, File

from nemo.collections.tts.models import TalkNetSpectModel
from nemo.collections.tts.models import TalkNetPitchModel
from nemo.collections.tts.models import TalkNetDursModel
from core.talknet_singer import TalkNetSingerModel
from core import extract, vocoder, reconstruct
from core.download import download_from_drive
from scipy.io import wavfile
from typing import Any

import tensorflow as tf
import numpy as np
import traceback
import ffmpeg
import torch
import json
import time
import uuid
import io
import os


tnmodels, tnmodel, tnpath, tndurs, tnpitch = {}, None, None, None, None
voc, last_voc, sr_voc, rec_voc = None, None, None, None


class Output(BaseModel):
    file: File

    
class Predictor(BasePredictor):
    def setup(self):
        """Load the model into memory to make running multiple predictions efficient"""
        self.DEVICE = "cuda:0"
        self.RUN_PATH = os.path.dirname(os.path.realpath(__file__))
        self.extract_dur = extract.ExtractDuration(self.RUN_PATH, self.DEVICE)

    def get_silent_wav(self):
        f = open("assets/silent.wav", "rb")
        buffer = io.BytesIO(f.read())
        return buffer

    def generate_audio(
        self,
        model,
        custom_model,
        transcript,
        pitch_options,
        pitch_factor,
        wav_name,
        f0s,
        f0s_wo_silence,
    ):
        playback_hide = {
            "display": "none",
        }
        global tnmodels, tnpath, tndurs, tnpitch, voc, last_voc, sr_voc, rec_voc

        if model is None:
            return [None, "No character selected", playback_hide, None]
        if transcript is None or transcript.strip() == "":
            return [
                None,
                "No transcript entered",
                playback_hide,
                None,
            ]
        load_error, talknet_path, vocoder_path = download_from_drive(
            model.split("|")[0], custom_model, self.RUN_PATH
        )
        if load_error is not None:
            return [
                None,
                load_error,
                playback_hide,
                None,
            ]

        with torch.no_grad():
            tnmodel = tnmodels.get(talknet_path)
            if tnmodel is None:
                # if tnpath != talknet_path:
                    singer_path = os.path.join(
                        os.path.dirname(talknet_path), "TalkNetSinger.nemo"
                    )
                    if os.path.exists(singer_path):
                        tnmodel = TalkNetSingerModel.restore_from(singer_path)
                    else:
                        tnmodel = TalkNetSpectModel.restore_from(talknet_path)
                    durs_path = os.path.join(
                        os.path.dirname(talknet_path), "TalkNetDurs.nemo"
                    )
                    pitch_path = os.path.join(
                        os.path.dirname(talknet_path), "TalkNetPitch.nemo"
                    )
                    if os.path.exists(durs_path):
                        tndurs = TalkNetDursModel.restore_from(durs_path)
                        tnmodel.add_module("_durs_model", tndurs)
                        tnpitch = TalkNetPitchModel.restore_from(pitch_path)
                        tnmodel.add_module("_pitch_model", tnpitch)
                    else:
                        tndurs = None
                        tnpitch = None
                    tnmodel.eval()
                    tnpath = talknet_path
                    tnmodels[talknet_path] = tnmodel

            # Generate spectrogram
            try:
                token_list, tokens, arpa = self.extract_dur.get_tokens(transcript)
                if tndurs is None or tnpitch is None:
                    return [
                        None,
                        "Model doesn't support pitch prediction",
                        playback_hide,
                        None,
                    ]
                spect = tnmodel.generate_spectrogram(tokens=tokens)
                # Vocoding
                if last_voc != vocoder_path:
                    voc = vocoder.HiFiGAN(vocoder_path, "config_v1", self.DEVICE)
                    last_voc = vocoder_path
                audio, audio_torch = voc.vocode(spect)

                # Reconstruction
                if "srec" in pitch_options:
                    new_spect = reconstruct_inst.reconstruct(spect)
                    if rec_voc is None:
                        rec_voc = vocoder.HiFiGAN(
                            os.path.join(self.RUN_PATH, "models", "hifirec"), "config_v1", self.DEVICE
                        )
                    audio, audio_torch = rec_voc.vocode(new_spect)

                # Super-resolution
                if sr_voc is None:
                    sr_voc = vocoder.HiFiGAN(
                        os.path.join(self.RUN_PATH, "models", "hifisr"), "config_32k", self.DEVICE
                    )
                sr_mix, new_rate = sr_voc.superres(audio, 22050)

                # Create buffer
                buffer = io.BytesIO()
                wavfile.write(buffer, new_rate, sr_mix.astype(np.int16))
                return buffer
            except IndexError:
                return self.get_silent_wav()

    def predict(self, s: str, voice: str) -> Any:
        # return self.generate_audio(voice + "|default", None, s, [], 0, None, None, None)

        response = None
        if s is None or s == "":
            response = self.get_silent_wav()
        else:
            if voice is None or voice == "":
                voice = "1k3EMXxLC0fLvfxzGbeP6B6plgu9hqCSx"
            response = self.generate_audio(voice + "|default", None, s, [], 0, None, None, None)

        return Output(file=response)
