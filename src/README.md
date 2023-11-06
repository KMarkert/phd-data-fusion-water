# src/

This README provides in details the setup and execution of scripts to replicate 
the results of the paper. There are two sub directories:

1. `python/`
2. `notebooks/`

The `python/` subdir hosts the scripts used to run the exports for surface water
mapping and to get the validation samples.

The `notebooks/` subdir hosts the notebooks used for analysis and validation 
process, including the visualization of figures from the paper.

## Environment setup

First step, create an vitual env to install the dependencies:

```sh
cd <select your workingdir>
python3 -m venv df-env
source df-env/bin/activate

pip install -r requirements.txt
```

>NOTE: You may also use conda to manage your environment. I used venv here becauase it has a smaller footprint and is quicker spin up with a small project like this.


If you have not authenticated with Earth Engine `earthengine authenticate`


## Running the data fusion

There are two steps to running the data fusion process:
1. Estimate surface water probability and maps from SAR data
2. Estimate surface water probability from Optical data and gap fill with SAR

Because we are using SAR data to gap fill the missing information from optical data, we need to run the SAR process first. 

Both data sources should be exported to the same collection so it will seems like all the same data in the end.

To run the SAR export use the following command:
```sh
python export_sar_fusion.py \
  --bbox xmin ymin xmax ymax \
  --start_time YYYY-MM-dd \
  --end_time YYYY-MM-dd \
  --target_ic projects/PROJECT_NAME/assets/ASSET_NAME
```

Where `bbox` is the bounding box of the region to crete the data, `start_time` and `end_time` are the respective beginning and end of record you would like to create the data for, and `target_ic` is the ImageCollection id that you would like to store the data to.

> IMPORTANT: Wait until all of the SAR image data are done exporting before even trying to run the optical exports. You don't want missing SAR data because that will effect the results of the optical gap filling!

Once the SAR data is done exporting you can run the optical exports using the following command:

```sh
python export_optical_fusion.py \
  --bbox xmin ymin xmax ymax \
  --start_time YYYY-MM-dd \
  --end_time YYYY-MM-dd \
  --target_ic projects/PROJECT_NAME/assets/ASSET_NAME
```

>NOTE: you will need to update the `project` argument in `ee.Initialize()` at line 12 to the cloud project you use with Earth Engine

The arguments are the same as before. Make sure `start_time` and `end_time` are the same or within what was exported for the SAR data becuase the optical export needs SAR data for the gap filling.

## Validation

The validation process requires the user to export the results from Google Earth
Engine into a CSV file for analysis. This is done using 

```
python export_validation.py
```

>NOTE: you will need to update the `project` argument in `ee.Initialize()` at line 9 to the cloud project you use with Earth Engine. Additionally, the ImageCollection asset name of the exported surface water maps at line 40. If you do not change the ImageCollection at line 40 it will use the public data from the paper.

All of the points for the validation are publicly available with the following asset path: `pro-jects/byu-hydroinformatics-gee/assets/kmarkert/datafusion/validation/<REGION>_all_wgs84` where `<REGION>` is the title case capitalized name of the regions (e.g. Colombia)

Once the validation export is complete there should be a file named `all_water_val.csv` in your Google Drive. This is what is used for the validation and vizualization notebooks.

### Visualizing results

