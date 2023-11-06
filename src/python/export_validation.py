import ee
import logging
import argparse

logging.basicConfig(level=logging.INFO)

# initialize earth engine
ee.Initialize(
    project="YOUR-PROJECT",
)

def main():
    def _sample_yr(yr):
        def _sample_mon(mon):
            t1 = ee.Date.fromYMD(yr, mon, 1);
            t2 = t1.advance(1, 'month');
            water = ic.select('water').filterDate(t1, t2).mean();
            points = labels.filter(
                ee.Filter.And(
                    ee.Filter.eq('year_ID', yr),
                    ee.Filter.eq('month_ID', mon)
                )  
            )
            return water.sampleRegions(
                collection= points,
                scale= 30,
                tileScale= 16,
                geometries= True
            );

        return months.map(_sample_mon);

    regions = ['Cambodia', 'Colombia', 'Gabon', 'Mexico', 'Myanmar', 'Zambia',]

    all_samples = ee.FeatureCollection([])

    for i, region in enumerate(regions):
        
        # load the data fusion image collection for region
        ic = ee.ImageCollection('projects/byu-hydroinformatics-gee/assets/kmarkert/datafusion/'+ region + '_data_fusion');

        # load the validation points for region
        labels = ee.FeatureCollection('projects/byu-hydroinformatics-gee/assets/kmarkert/datafusion/validation/' + region + '_all_wgs84');
        
        # get the unique years and months from the validation points
        years = labels.aggregate_array('year_ID').distinct();
        months = labels.aggregate_array('month_ID').distinct();
        
        # sample for each year and month
        _samples = years.map(_sample_yr);
        
        # flatten the list of lists
        samples = ee.FeatureCollection(_samples.flatten()).flatten();
        
        # add region samples to all samples
        all_samples = all_samples.merge(samples)

    # remove samples with no water
    all_samples = all_samples.filter(ee.Filter.neq('water', None))

    # threshold mean water values from image mosaic to water value
    all_samples = all_samples.map(lambda f:
        f.set('water_f', ee.Number(f.get('water')).gt(0.5))
    )

    logging.debug(f' Sample size: {all_samples.size().getInfo()}')

    # create confusion matrix from samples
    cm = all_samples.errorMatrix('wat', 'water');

    # export samples to drive
    task = ee.batch.Export.table.toDrive(
        collection= all_samples,
        description= 'data_fusion_water_samples',
        fileNamePrefix= 'all_water_val'
    )
    task.start()
    logging.info(f' Started task with ID {task.status()["id"]}')


if __name__ == "__main__":
    main()