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
SAR_COEFS = ee.Image(
    [
        -0.05876135869889348, 
        -0.03920879220095715, 
        0.14396553677593862, 
        0.49006037880688985, 
        -0.4439070142915821, 
        0.42711910382835183, 
        -0.0038731568155142815, 
        -10.99308715043533
    ]
).float()

# const variable of JRC monthly image collection
JRC_MONTHLY = ee.ImageCollection("JRC/GSW1_4/MonthlyRecurrence")

# helper function to rescale proba to 0-100 and convert to uint8
def rescale(img):
    p_rescale = img.select("proba").multiply(100)
    return img.addBands(p_rescale, None, True).uint8()

# helper function to calculate VV/VH ratio
def s1ratio(img):
    return img.addBands(img.select("VV").divide(img.select("VH")).rename("VVVH"))

# helper function to calculate neighborhood mean/stdDev
def s1neighborhood(img):
    return img.addBands(img.select('V.*').reduceNeighborhood(
            reducer= ee.Reducer.mean().combine(ee.Reducer.stdDev(), None, True),
            kernel= ee.Kernel.square(4.5, 'pixels'),
            # optimization= 'window'
        ))

# function to apply sigmoid to prediction
def sigmoid(y_pred):
    y_pred_neg = y_pred.multiply(-1)
    x = ee.Image.cat([y_pred_neg, y_pred])

    sigmoid_proba = ee.Image(1).divide(ee.Image(1).add(x.multiply(-1).exp()))

    out_proba = sigmoid_proba.divide(sigmoid_proba.reduce("sum")).select(
        [1]
    )

    return out_proba

def s1_pred(img):
    occurrence = (
        JRC_MONTHLY.filter(
            ee.Filter.eq('month',img.date().get('month'))
        )
        .first()
        .select('monthly_recurrence')
    )
    max_extent = occurrence.mask().And(occurrence.gt(0).unmask(0))

    y_pred = img.select(['V.*']).addBands(ee.Image(1)).multiply(SAR_COEFS).reduce("sum").focal_mean()

    prediction = (
        sigmoid(y_pred)
        .multiply(max_extent)
        .updateMask(img.select([0]).mask())
        .rename("proba")
    )

    water = hf.edge_otsu(
        prediction, 
        initial_threshold=0.5, 
        scale=300, 
        thresh_no_data=0.5,
        edge_buffer=300,
        region=img.geometry(1e3),
        invert=True
    ).rename("water")

    return img.select().addBands(ee.Image.cat([prediction, water])).clip(img.geometry(1e3).bounds(1e3))


def sar_fusion(bbox, start_time, end_time, target_ic):
    
    # create a new image collection
    # this is not necessary if one already exists
    try:
        ee.data.createAsset({'type':'ImageCollection'}, target_ic)
    except ee.EEException as e:
        if 'Cannot overwrite asset' in str(e):
            logging.info('ImageCollection asset exists skipping creation...')
        else:
            logging.error(e)


    logging.info(f"Time range: {start_time} - {end_time}")

    geometry = ee.Geometry.Rectangle(bbox)

    s1_ds = hf.Sentinel1(geometry, ee.Date(start_time).advance(-1,'month'), end_time)

    logging.info(f"Number of Sentinel-1 images: {s1_ds.n_images}")
    img_names = s1_ds.collection.aggregate_array('system:index').getInfo()

    merit = ee.Image("MERIT/DEM/v1_0_3").unmask(0)

    pipeline = (
        (hf.slope_correction, dict(elevation=merit, buffer=50)),
        # hf.gamma_map,
        s1neighborhood,
        s1ratio,
    )

    s1_ds.pipe(pipeline, inplace=True)

    s1_preds = s1_ds.apply_func(s1_pred)

    n = s1_ds.n_images# merged.limit(10).size().getInfo()

    export_list = s1_preds.collection.toList(n)

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
            date = id.split('/')[-1].split('_')[1]
            img_name = id.split('/')[-1]


        assetid = f"{target_ic}/{img_name}"

        task = ee.batch.Export.image.toAsset(
            image=ee.Image(export_img),
            description = f"export_{sensor}_{date}",
            assetId = assetid,
            region = export_img.geometry(),
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
        help="Target asset id to store results",
        required=True,
    )
    
    args = parser.parse_args()

    # pass into optical_fusion function
    sar_fusion(args.bbox, args.start_time, args.end_time, args.target_ic)

    return


if __name__ == "__main__":
    main()