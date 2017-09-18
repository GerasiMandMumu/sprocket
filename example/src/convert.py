#! /usr/bin/env python
# -*- coding: utf-8 -*-

"""
Conversion

"""

from __future__ import absolute_import, division, print_function

import argparse
import os
import sys
import numpy as np

import pysptk
from pysptk.synthesis import MLSADF
from scipy.io import wavfile
from sklearn.externals import joblib

from .yml import PairYML, SpeakerYML
from sprocket.speech import FeatureExtractor, Synthesizer
from sprocket.model import GMMConvertor, F0statistics, GV
from sprocket.util import static_delta, HDF5


def main(*argv):
    argv = argv if argv else sys.argv[1:]
    # Options for python
    description = 'estimate joint feature of source and target speakers'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-gmmmode', '--gmmmode', type=str, default=None,
                        help='mode of the GMM [None, diff, or intra]')
    parser.add_argument('org', type=str,
                        help='Original speaker')
    parser.add_argument('tar', type=str,
                        help='Original speaker')
    parser.add_argument('org_yml', type=str,
                        help='Yml file of the original speaker')
    parser.add_argument('pair_yml', type=str,
                        help='Yml file of the speaker pair')
    parser.add_argument('eval_list_file', type=str,
                        help='List file for evaluation')
    parser.add_argument('wav_dir', type=str,
                        help='Directory path of source spekaer')
    parser.add_argument('pair_dir', type=str,
                        help='Directory path of pair directory')
    args = parser.parse_args(argv)

    # read parameters from speaker yml
    sconf = SpeakerYML(args.org_yml)
    pconf = PairYML(args.pair_yml)

    # read GMM for mcep
    mcepgmmpath = os.path.join(args.pair_dir, 'model/GMM.pkl')
    mcepgmm = GMMConvertor(n_mix=pconf.GMM_mcep_n_mix,
                           covtype=pconf.GMM_mcep_covtype,
                           gmmmode=args.gmmmode,
                           )
    param = joblib.load(mcepgmmpath)
    mcepgmm.open_from_param(param)
    print("conversion mode: {}".format(args.gmmmode))

    # read F0 statistics and GV
    stats_dir = os.path.join(args.pair_dir, 'stats')
    orgstatspath = os.path.join(stats_dir,  args.org + '.h5')
    orgstats_h5 = HDF5(orgstatspath, mode='r')
    orgf0stats = orgstats_h5.read(ext='f0stats')
    orgstats_h5.close()

    tarstatspath = os.path.join(stats_dir,  args.tar + '.h5')
    tarstats_h5 = HDF5(tarstatspath, mode='r')
    tarf0stats = tarstats_h5.read(ext='f0stats')
    gvstats = tarstats_h5.read(ext='gv')
    tarstats_h5.close()

    mcepgv = GV()
    f0stats = F0statistics()

    # constract FeatureExtractor class
    feat = FeatureExtractor(analyzer=sconf.analyzer,
                            fs=sconf.wav_fs,
                            shiftms=sconf.wav_shiftms,
                            minf0=sconf.f0_minf0,
                            maxf0=sconf.f0_maxf0)

    # open synthesizer
    synthesizer = Synthesizer()
    shiftl = int(sconf.wav_fs / 1000 * sconf.wav_shiftms)
    mlsa_fil = pysptk.synthesis.Synthesizer(
        MLSADF(order=sconf.mcep_dim, alpha=sconf.mcep_alpha), shiftl)

    # test directory
    test_dir = os.path.join(args.pair_dir, 'test')
    if not os.path.exists(os.path.join(test_dir, args.org)):
        os.makedirs(os.path.join(test_dir, args.org))

    # conversion in each evaluation file
    with open(args.eval_list_file, 'r') as fp:
        for line in fp:
            # open wav file
            f = line.rstrip()
            wavf = os.path.join(args.wav_dir, f + '.wav')
            fs, x = wavfile.read(wavf)
            x = np.array(x, dtype=np.float)
            assert fs == sconf.wav_fs

            # analyze F0, mcep, and ap
            f0, spc, ap = feat.analyze(x)
            mcep = feat.mcep(dim=sconf.mcep_dim, alpha=sconf.mcep_alpha)
            mcep_0th = mcep[:, 0]

            print('convert ' + f)
            # convert F0
            cvf0 = f0stats.convert(f0, orgf0stats, tarf0stats)

            # convert mel-cepstrum
            cvmcep_wopow = mcepgmm.convert(static_delta(mcep[:, 1:]),
                                           cvtype=pconf.GMM_mcep_cvtype)
            cvmcep = np.c_[mcep_0th, cvmcep_wopow]

            # synthesis VC w/ GV
            if args.gmmmode is None:
                cvmcep_wGV = mcepgv.postfilter(cvmcep, gvstats, startdim=1)
                wav = synthesizer.synthesis(cvf0,
                                            cvmcep_wGV,
                                            ap,
                                            alpha=sconf.mcep_alpha,
                                            fftl=sconf.wav_fftl,
                                            fs=sconf.wav_fs)

                wav = np.clip(wav, -32768, 32767)
                wavpath = os.path.join(test_dir, f + '_VC.wav')

            # synthesis DIFFVC w/ GV
            if args.gmmmode == 'diff':
                cvmcep[:, 0] = 0.0
                cvmcep_wGV = mcepgv.postfilter(
                    mcep + cvmcep, gvstats, startdim=1) - mcep
                b = np.apply_along_axis(pysptk.mc2b, 1, cvmcep_wGV, sconf.mcep_alpha)
                assert np.isfinite(b).all()
                x = x.astype(np.float64)
                wav = mlsa_fil.synthesis(x, b)
                wav = np.clip(wav, -32768, 32767)
                wavpath = os.path.join(test_dir, f + '_DIFFVC.wav')

            # write waveform
            wavfile.write(
                wavpath, fs, np.array(wav, dtype=np.int16))


if __name__ == '__main__':
    main()
