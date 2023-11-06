import ee
import logging
from tqdm import tqdm
from retry import retry
import hydrafloods as hf

import argparse

logging.basicConfig(level=logging.INFO)

ee.Initialize(
    project="YOUR-PROJECT",
)

# const variables for coefficients of logistic regression
OPT_COEFS = ee.Image(
    [
        -18.967214469349052, 
        38.400708323537344, 
        25.49189114765653, 
        -22.58952137305692, 
        -5.108155013068395, 
        -25.101566051936846, 
        4.50423157543959, 
        1.0219745060336451
    ]
)

# helper function to rescale proba to 0-100 and convert to uint8
def rescale(img):
    p_rescale = img.select("proba").multiply(100)
    return img.addBands(p_rescale, None, True).uint8()

# helper function to add mndwi band to image
def mdnwi(img):
    mndwi_img = hf.mndwi(img)
    return img.addBands(mndwi_img)


# function to apply sigmoid to prediction
def sigmoid(y_pred):
    y_pred_neg = y_pred.multiply(-1)
    x = ee.Image.cat([y_pred_neg, y_pred])

    sigmoid_proba = ee.Image(1).divide(ee.Image(1).add(x.multiply(-1).exp()))

    out_proba = sigmoid_proba.divide(sigmoid_proba.reduce("sum")).select(
        [1]
    )

    return out_proba

# function to predict water proability from optical image
def lc8_pred(img):
    y_pred = img.addBands(ee.Image(1)).multiply(OPT_COEFS).reduce("sum")

    prediction = (
        sigmoid(y_pred).updateMask(img.select([0]).mask())
        # .multiply(100)
        .rename("proba")
    )

    return img.select().addBands(prediction).clip(img.geometry(1e3).bounds(1e3))  # .uint8();

def optical_fusion(bbox, start_time, end_time, target_ic):

    logging.info(f"Time range: {start_time} - {end_time}")

    geometry = ee.Geometry.Rectangle(bbox)

    s1_preds = (
        ee.ImageCollection(target_ic)
        .filter(
            ee.Filter.stringStartsWith(
                'system:index',
                'S1'
            )
        )
    )

    lc8_ds = hf.Landsat8(geometry, start_time, end_time)
    lc8_ds.collection = (
        lc8_ds.collection
        .filter(ee.Filter.lt("CLOUD_COVER", 75))
        .map(mdnwi)
    )

    logging.info(f"Number of Landsat 8 images: {lc8_ds.n_images}")
    img_names = lc8_ds.collection.aggregate_array('system:index').getInfo()

    lc8_preds = lc8_ds.apply_func(lc8_pred)

    def fillLc8pred(img):
        init_water = hf.edge_otsu(
            img.select("proba"), 
            initial_threshold=0.5, 
            scale=300, 
            thresh_no_data=0.5,
            edge_buffer=300,
            region=img.geometry(1e3),
            invert=True
        )
        edges = ee.Algorithms.CannyEdgeDetector(init_water, 0.1, 0.5)

        date = img.date()
        s1_probas = (
            s1_preds.filterBounds(img.geometry(1e3))
            .filterDate(date.advance(-15, "day"), date.advance(1, "day"))
            .sort("system:time_start", False)
            .select("proba")
        )

        s1_proba = s1_probas.reduce(ee.Reducer.firstNonNull())

        s1_proba = (
            s1_proba.addBands(ee.Image(1e-3)).reduce(ee.Reducer.max()).rename("proba_first")
        )

        # get water probability around edges
        # P(D|W) = P(D|W) * P(W) / P(D) ~=  P(D|W) * P(W)
        fillPercentile = 50
        # we use 50% when using JRC (quality is not good)
        p = (
            s1_proba.updateMask(edges.fastDistanceTransform().lt(30).And(init_water))
            .reduceRegion(
                reducer=ee.Reducer.percentile([fillPercentile]),
                geometry=img.geometry(1e3),
                scale=30,
                maxPixels=1e9,
            )
            .values()
            .get(0)
        )

        ps = ee.List([p, 5]).reduce(ee.Reducer.max())

        waterMask = init_water.unmask(s1_proba.gt(ee.Image.constant(ps)))#.clip(img.geometry())

        return img.addBands(waterMask.rename("water").uint8()).set(
            "p_thresh", ps, "usable", s1_probas.size()
        )


    lc8_fill = lc8_preds.apply_func(fillLc8pred)

    n = lc8_ds.n_images# merged.limit(10).size().getInfo()

    export_list = lc8_fill.collection.toList(n)


    @retry(tries=10, delay=1, backoff=2)
    def run_export_img(i):
        export_img = rescale(ee.Image(export_list.get(i)))
        # id = export_img.get('system:index').getInfo()
        id = img_names[i]
        if 'S1' in id:
            sensor = 'S1'
            date = id.split('/')[-1].split('_')[4]
            img_name =id.split('/')[-1]

        elif 'LC08' in id:
            sensor = 'LC08'
            date = id.split('/')[-1].split('_')[2]
            img_name = id.split('/')[-1]


        assetid = f"{target_ic}/{img_name}"

        #
        task = ee.batch.Export.image.toAsset(
            image=ee.Image(export_img),
            description = f"export_{sensor}_{date}",
            assetId = assetid,
            region = export_img.geometry(1e3),
            scale = 30,
            crs = "EPSG:4326",
            maxPixels = 1e13,
            pyramidingPolicy = {"proba": "mean", "water": "mode"},
        )
        task.start()


    for i in tqdm(range(n)):
        run_export_img(i)

    return
    
def main():
    # argparse section to read in arguments for start and end date as well as the bounding box
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--bbox",
        type=float,
        nargs='+',
        help="Bounding box in format min_lon,min_lat,max_lon,max_lat",
        required=True,
    )
    parser.add_argument(
        "--start_time",
        type=str,
        help="Start date in format YYYY-MM-DD",
        required=True,
    )
    parser.add_argument(
        "--end_time",
        type=str,
        help="End date in format YYYY-MM-DD",
        required=True,
    )
    parser.add_argument(
        "--target_ic",
        type=str,
        help="Target asset id to store results, must have S1 exports already",
        required=True,
    )
    
    args = parser.parse_args()

    # pass into optical_fusion function
    optical_fusion(args.bbox, args.start_time, args.end_time, args.target_ic)

    return


if __name__ == "__main__":
    main()