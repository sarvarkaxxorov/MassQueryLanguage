import json
import os
import argparse
import glob
import sys
import pandas as pd
import numpy as np
import pymzml
from pyteomics import mzxml
from psims.mzml.writer import MzMLWriter

def main():
    parser = argparse.ArgumentParser(description="MSQL Query in Proteosafe")
    parser.add_argument('input_folder', help='Input filename')
    parser.add_argument('results_file', help='Input Query')
    parser.add_argument('extracted_mgf', help='extracted_mgf')
    parser.add_argument('extracted_mzML', help='extracted_mgf')
    parser.add_argument('extracted_result', help='extracted_mgf')

    args = parser.parse_args()

    if os.path.isdir(args.results_file):
        results_df = pd.read_csv(glob.glob(os.path.join(args.results_file, "*"))[0], sep='\t')
    else:
        results_df = pd.read_csv(args.results_file, sep='\t')

    try:
        _extract_spectra(results_df, 
                        args.input_folder, 
                        output_mgf_filename=args.extracted_mgf,
                        output_mzML_filename=args.extracted_mzML,
                        output_summary=args.extracted_result)
    except Exception as e: 
        print(e)
        print("FAILURE ON EXTRACTION")
        pass

def _extract_mzML_scan(input_filename, spectrum_identifier):
    MS_precisions = {
        1: 5e-6,
        2: 20e-6,
        3: 20e-6,
        4: 20e-6,
        5: 20e-6,
        6: 20e-6,
        7: 20e-6,
    }
    
    run = pymzml.run.Reader(input_filename, MS_precisions=MS_precisions)

    try:
        for spec in run:
            if str(spec.ID) == str(spectrum_identifier):
                break
    except:
        raise

    peaks = spec.peaks("raw")

    # Filtering out zero rows
    peaks = peaks[~np.any(peaks < 1.0, axis=1)]

    # Sorting by intensity
    peaks = peaks[peaks[:, 1].argsort()]

    # Getting top 1000
    #peaks = peaks[-1000:]

    if len(peaks) == 0:
        return None

    mz, intensity = zip(*peaks)

    mz_list = list(mz)
    i_list = list(intensity)

    peaks_list = []
    for i in range(len(mz_list)):
        peaks_list.append([mz_list[i], i_list[i]])

    # Sorting Peaks
    peaks_list = sorted(peaks_list, key=lambda x: x[0])

    spectrum_obj = {}
    spectrum_obj["peaks"] = peaks_list
    spectrum_obj["mslevel"] = spec.ms_level
    spectrum_obj["scan"] = spectrum_identifier

    if spec.ms_level > 1:
        msn_mz = spec.selected_precursors[0]["mz"]
        spectrum_obj["precursor_mz"] = msn_mz

    return spectrum_obj

def _extract_mzXML_scan(input_filename, spectrum_identifier):
    with mzxml.read(input_filename) as reader:
        for spectrum in reader:
            if str(spectrum["id"]) == str(spectrum_identifier):
                spec = spectrum
                break

        mz_list = list(spec["m/z array"])
        i_list = list(spec["intensity array"])

        peaks_list = []
        for i in range(len(mz_list)):
            peaks_list.append([mz_list[i], i_list[i]])

        # Sorting Peaks
        peaks_list = sorted(peaks_list, key=lambda x: x[0])

        # Loading Data
        spectrum_obj = {}
        spectrum_obj["peaks"] = peaks_list
        spectrum_obj["mslevel"] = spec["msLevel"]
        spectrum_obj["scan"] = spectrum_identifier

        if spec["msLevel"] > 1:
            msn_mz = spec["precursorMz"][0]["precursorMz"]
            spectrum_obj["precursor_mz"] = msn_mz

        return spectrum_obj

def _extract_spectra(results_df, input_spectra_folder, 
                    output_mgf_filename=None, 
                    output_mzML_filename=None, 
                    output_summary=None):
    spectrum_list = []

    # TODO: reduce duplicate scans to extract

    current_scan = 1
    results_list = results_df.to_dict(orient="records")
    for result_obj in results_list:
        if "mangled_filename" in result_obj:
            input_spectra_filename = os.path.join(input_spectra_folder, result_obj["mangled_filename"])
        else:
            input_spectra_filename = os.path.join(input_spectra_folder, result_obj["filename"])

        scan_number = result_obj["scan"]

        spectrum_obj = None
        if ".mzML" in input_spectra_filename:
            spectrum_obj = _extract_mzML_scan(input_spectra_filename, scan_number)
        if ".mzXML" in input_spectra_filename:
            spectrum_obj = _extract_mzXML_scan(input_spectra_filename, scan_number)

        if spectrum_obj is not None:
            spectrum_obj["new_scan"] = current_scan
            result_obj["new_scan"] = current_scan

            spectrum_list.append(spectrum_obj)
            current_scan += 1

    # Writing the updated extraction
    if output_summary is not None:
        df = pd.DataFrame(results_list)
        df.to_csv(output_summary, sep='\t', index=False)

    # Writing the spectrum now
    with open(output_mgf_filename, "w") as o:
        for i, spectrum in enumerate(spectrum_list):
            o.write("BEGIN IONS\n")
            if "precursor_mz" in spectrum:
                o.write("PEPMASS={}\n".format(spectrum["precursor_mz"]))
            o.write("SCANS={}\n".format(spectrum["new_scan"]))
            for peak in spectrum["peaks"]:
                o.write("{} {}\n".format(peak[0], peak[1]))
            o.write("END IONS\n")

    if output_mzML_filename is not None:
        with MzMLWriter(open(output_mzML_filename, 'wb'), close=True) as out:
            # Add default controlled vocabularies
            out.controlled_vocabularies()
            # Open the run and spectrum list sections
            with out.run(id="my_analysis"):
                spectrum_count = len(spectrum_list)
                with out.spectrum_list(count=spectrum_count):
                    for spectrum in spectrum_list:
                        mz_list = [peak[0] for peak in spectrum["peaks"]]
                        i_list = [peak[1] for peak in spectrum["peaks"]]
                        mslevel = spectrum["mslevel"]

                        if mslevel == 1:
                            # Write Precursor scan
                            out.write_spectrum(
                                mz_list, i_list,
                                id="scan={}".format(spectrum["new_scan"]), params=[
                                    "MS1 Spectrum",
                                    {"ms level": 1},
                                    {"total ion current": sum(i_list)}
                                ])
                        elif mslevel == 2:
                            out.write_spectrum(
                                mz_list, i_list,
                                id="scan={}".format(spectrum["new_scan"]), params=[
                                    "MSn Spectrum",
                                    {"ms level": 2},
                                    {"total ion current": sum(i_list)}
                                ],
                                # Include precursor information
                                precursor_information={
                                    "mz": spectrum["precursor_mz"],
                                    "intensity": 0,
                                    "charge": 0,
                                    "scan_id": 0,
                                    "activation": ["beam-type collisional dissociation", {"collision energy": 25}],
                                    "isolation_window": [spectrum["precursor_mz"] - 1, spectrum["precursor_mz"], spectrum["precursor_mz"] + 1]
                                })


if __name__ == "__main__":
    main()