import os
import imageio
import numpy as np
import rasterio
import src.models.analyze_model
from skimage import io as skio
import warnings
import glob
import pandas as pd


class PredImg():
    def __init__(self, num_labels=1, origin_r=0, origin_c=0):
        # paths
        self.imag_path = imag_str
        self.save_path = './results/'
        self.origin = [origin_r, origin_c]
        # labels
        self.num_labels = num_labels
        if not os.path.exists(self.save_path):
            os.makedirs(self.save_path)

    def set_geometry(self):
        # set the geometry
        self.width = np.shape(self.image)[1]
        self.height = np.shape(self.image)[0]
        self.channels = np.shape(self.image)[2]
        self.geo_scale = [self.width, self.height]

    def load_imag_from_path(self, imag_path):
        # read in image
        self.image = imageio.imread(imag_path)

    def set_image(self, image):
        # set image
        self.image = image

    def write2file(self, data, fileid):
        filename = self.image_path + fileid
        f = open(filename, 'w+')
        for line in data:
            f.write(str(line) + '\n')

    def writelocalbox2file(self):
        self.write2file(self.boxes, 'bb_local.txt')

    def writeglobalbox2file(self):
        self.write2file(self.boxes_global, 'bb_global.txt')

    def pred_boxes(self):
        self.boxes = np.empty([0, 6])

    def get_boxes_in_orig(self):
        self.boxes_global = np.ones_like(self.boxes)
        # scale boxes to row columns (and relative to origin)
        self.boxes_global[:, 1] = (
            (self.boxes[:, 1] * self.scale[1]) + self.origin[0])
        self.boxes_global[:, 2] = (
            (self.boxes[:, 2] * self.scale[2]) + self.origin[1])
        self.boxes_global[:, 3] = (self.boxes[:, 3] * self.scale[0])
        self.boxes_global[:, 4] = (self.boxes[:, 4] * self.scale[1])


class PredImgRandom(PredImg):
    def __init__(self, imag_str, pix_scale=255., origin_r=0, origin_c=0):
        PredImg.__init__(self, origin_r=origin_r, origin_c=origin_c)

    def pred_boxes(self):
        num_objects = np.random.randint(3)+1
        self.boxes = np.random.rand(num_objects, 4)
        self.confidence = np.random.rand(num_objects,)
        self.labels = np.random.choice(self.num_labels, num_objects)


class SatelliteTif():
    def __init__(self, tif_path, rel_path_2_data, rel_path_2_output,
                 c_channels=[0, 1, 3], sub_img_w=200, sub_img_h=200,
                 train_window=0.4, num_train=100, valid_frac=0.3,
                 r_start=0, r_end=np.inf, c_start=0, c_end=np.inf):
        # store tif
        self.sat_data = rasterio.open(tif_path)
        # store geometry conversion type
        self.crs = self.sat_data.crs
        # lat/lon project string
        self.lonlat_proj = 'epsg:4326'
        # set geometry
        self.tif_norm = 65535.
        self.jpg_norm = 355
        self.c_channels = c_channels
        self.sat_w = self.sat_data.width
        self.sat_h = self.sat_data.height
        self.sat_c = self.sat_data.count
        self.img_w = sub_img_w
        self.img_h = sub_img_h
        self.img_c = len(c_channels)
        # set up save str
        leading_zeros = int(np.ceil(np.log10(np.max([self.sat_w,
                                                     self.sat_h]))))
        self.classes = {'0': 'trees'}
        self.inv_classes = {'trees': 0}
        self.image_save_format = (
            'image_{:0' + str(leading_zeros) + 'd}' +
            '_{:0' + str(leading_zeros) + 'd}')
        # store all directories
        self.base_dir = os.getcwd() + '/' + rel_path_2_data
        self.pred_dir = self.base_dir + '/predict'
        self.pred_collect_dir = self.pred_dir + '/bb_info'
        self.train_dir = self.base_dir + '/train'
        self.valid_dir = self.base_dir + '/valid'
        self.output_dir = os.getcwd() + '/' + rel_path_2_output
        self.build_directories()
        # set prediction class and train params
        self.valid_frac = valid_frac
        # get orgins for each subset
        self.r_end = np.min([r_end, self.sat_h])
        self.r_start = r_start
        self.c_end = np.min([c_end, self.sat_w])
        self.c_start = c_start
        self.pred_origins = self.build_origins(self.r_start,
                                               self.r_end,
                                               self.c_start,
                                               self.c_end)
        # got training, sample from some fraction of the center of image
        delta_window_start = (1-train_window) / 2
        delta_window_end = 1 - (1-train_window) / 2
        poss_train_origins = self.build_origins(delta_window_start*self.sat_h,
                                                delta_window_end*self.sat_h,
                                                delta_window_start*self.sat_w,
                                                delta_window_end*self.sat_w)
        # get random sample
        max_train_num = len(poss_train_origins)
        num_train = np.min([num_train, max_train_num])
        self.num_all = num_train
        self.num_valid = int(self.valid_frac * num_train)
        self.num_train = self.num_all - self.num_valid
        idx = np.random.choice(np.arange(max_train_num),
                               size=self.num_all, replace=False)
        self.training_origins = poss_train_origins[idx, :]
        self.train_origins = self.training_origins[:self.num_train, :]
        self.valid_origins = self.training_origins[self.num_train:, :]

    def collect_outputs(self, save_name=None):
        # build a list of all bounding boxes
        files = glob.glob(self.pred_collect_dir + '/*csv')
        df_columns = ['fname_full', 'fname', 'label', 'imag_w', 'imag_h',
                      'x', 'y', 'w', 'h', 'conf',
                      'r_origin', 'c_origin', 'r_local', 'c_local', 'r_global',
                      'c_global']
        file_cols = ['label', 'imag_w', 'imag_h', 'x', 'y', 'w', 'h', 'conf']
        self.df_all = pd.DataFrame(index=np.arange(0), columns=df_columns)
        for a_file in files:
            # get r,c from file
            file_id = a_file.split('/')[-1]
            (r_org, c_org) = map(int, file_id[:-4].split('_')[-2:])
            # set up df
            df_temp = pd.read_csv(a_file)
            num_rows = len(df_temp)
            # build a temp df of the correct shape
            df_sub = pd.DataFrame(index=np.arange(num_rows),
                                  columns=df_columns)
            df_sub[file_cols] = df_temp[file_cols]
            df_sub['fname_full'] = a_file
            df_sub['fname'] = a_file.split('/')[-1]
            # calculate the rest
            df_sub['r_origin'] = r_org
            df_sub['c_origin'] = c_org
            df_sub['r_local'] = (df_sub['y'] * df_sub['imag_h']).astype(int)
            df_sub['c_local'] = (df_sub['x'] * df_sub['imag_w']).astype(int)
            df_sub['r_global'] = df_sub['r_local'] + df_sub['r_origin']
            df_sub['c_global'] = df_sub['c_local'] + df_sub['c_origin']
            # append it
            self.df_all = self.df_all.append(df_sub, ignore_index=True)
            # save it
            if save_name is not None:
                self.df_all.to_csv(self.output_dir + '/' + save_name)

    def build_directories(self):
        dirs2build = [self.base_dir, self.pred_dir,
                      self.train_dir, self.valid_dir,
                      self.output_dir]
        for a_dir in dirs2build:
            # build dirs
            if not os.path.exists(a_dir):
                os.makedirs(a_dir)
                # build image dirs
                if a_dir not in [self.base_dir, self.output_dir]:
                    image_dir = a_dir+'/images'
                    if not os.path.exists(image_dir):
                        os.makedirs(image_dir)
                    # build label dirs
                    if a_dir in [self.train_dir, self.valid_dir]:
                        label_dir = a_dir+'/labels'
                        if not os.path.exists(label_dir):
                            os.makedirs(label_dir)

    def proj_lonlat_2_rc(self, lonlat):
        '''
        Description:
            Convert lon/lat coordinates to rows and columns in the tif
            satellite image. Uses pyproj to convert between coordinate systems
        Args:
            lonlat (np.array, size=[n,2]): array of longitude
                (col1) and lat (col2)
        Returns:
            rc (np.array shape=[n, 2]): row/columns in tif file for all
                recorded points
        Updates:
            N/A
        Write to file:
            N/A
        '''
        # input lat/lon
        in_proj = pyproj.Proj(init=self.lonlat_proj)
        # output based on crs of tif/shp
        out_proj = pyproj.Proj(dataset.crs)
        # transform lat/lon to xy
        x, y = pyproj.transform(in_proj, out_proj, lonlat[:, 0], lonlat[:, 1])
        # convert rows and columns to xy map project
        (r, c) = rasterio.transform.rowcol(dataset.transform, x, y)
        # store it in numpy array
        rc = np.array([r, c]).transpose()
        return rc

    def proj_rc_2_lonlat(self, rc):
        '''
        Description:
            Convert row/columns of tif dataset to lat/lon.
            Uses pyproj to convert between coordinate systems
        Args:
            rc (np.array shape=[n, 2]): row/columns in tif file for all
                recorded points
        Returns:
            lonlat (np.array, size=[n,2]): array of longitude (col1)
                and lat (col2)
        Updates:
            N/A
        Write to file:
            N/A
        '''
        # convert rows and columns to xy map project
        (x, y) = rasterio.transform.xy(dataset.transform, rc[:, 0], rc[:, 1])
        # input based on crs of tif/shp
        in_proj = pyproj.Proj(self.crs)
        # output lat/lon
        out_proj = pyproj.Proj(init=self.lonlat_proj)
        # transform xy to lat/lon
        lon, lat = pyproj.transform(in_proj, out_proj, x, y)
        # store it in numpy array
        lonlat = np.array([lon, lat]).transpose()
        return lonlat

    def clean_images(self, path):
        # DANGER!!!
        files = glob.glob(path + '/*jpg')
        for f in files:
            os.remove(f)

    def clean_pred_images(self):
        # DANGER!!!
        self.clean_images(self.pred_dir + '/images')

    def clean_train_images(self):
        # DANGER!!!
        self.clean_images(self.train_dir + '/images')

    def clean_valid_images(self):
        # DANGER!!!
        self.clean_images(self.valid_dir + '/images')

    def build_origins(self, start_r, end_r, start_c, end_c):
        # set start and end to start/end on a division point
        start_c = np.floor(start_c/self.img_w) * self.img_w
        end_c = np.floor(end_c/self.img_w) * self.img_w
        start_r = np.floor(start_r/self.img_h) * self.img_h
        end_r = np.floor(end_r/self.img_h) * self.img_h
        # get all origins
        row_origins = np.arange(start_r, end_r, self.img_h)
        col_origins = np.arange(start_c, end_c, self.img_w)
        origin_list = np.array([(r, c) for r in row_origins
                                for c in col_origins]).astype(int)
        return origin_list

    def build_train_dataset(self):
        self.build_dataset(self.train_origins, self.train_dir)

    def build_valid_dataset(self):
        self.build_dataset(self.valid_origins, self.valid_dir)

    def build_pred_dataset(self):
        self.build_dataset(self.pred_origins, self.pred_dir)

    def build_dataset(self, origins, save_dir):
        # build full array (r_start, r_end, c_start, c_end)
        all_coors = np.zeros((len(origins), 4)).astype(int)
        # build all coordinat (row_start, row_end, col_start, col_end)
        all_coors[:, 0] = origins[:, 0]
        all_coors[:, 1] = origins[:, 0] + self.img_h
        all_coors[:, 2] = origins[:, 1]
        all_coors[:, 3] = origins[:, 1] + self.img_w
        # get key name from save dir
        save_repo = save_dir.split('/')[-1]
        image_path = save_dir + '/images/'
        lookup_filename = save_dir + '/' + save_repo + '_key.txt'
        with open(lookup_filename, 'w+') as look_up_f:
            for (org_id, origin) in enumerate(origins):
                # set image save name
                img_save_id = self.image_save_format.format(origin[0],
                                                            origin[1])
                # get subset
                band_data = self.get_subset(all_coors[org_id, 0],
                                            all_coors[org_id, 1],
                                            all_coors[org_id, 2],
                                            all_coors[org_id, 3])
                # don't save it if it's blank
                if np.all(band_data == np.zeros((self.img_h,
                                                 self.img_w,
                                                 self.img_c))):
                    pass
                else:
                    # update band to save based on color channels
                    # must be saved as integars from 0, 255
                    band2save = np.array(band_data).astype('uint8')
                    # save everything
                    img_save_name = img_save_id + '.jpg'
                    # save image
                    imageio.imwrite(image_path + img_save_name, band2save)
                    # update lookup txt file
                    line2save = (img_save_name
                                 + ' ' + str(all_coors[org_id, 0])
                                 + ' ' + str(all_coors[org_id, 1])
                                 + ' ' + str(all_coors[org_id, 2])
                                 + ' ' + str(all_coors[org_id, 3])
                                 + '\n')

                    look_up_f.write(line2save)

    def get_subset(self, r_start, r_end, c_start, c_end):
        data = np.zeros((self.img_h, self.img_w, self.img_c))
        for (c_id, c) in enumerate(self.c_channels):
            data[:, :, c_id] = self.sat_data.read(
                c+1, window=((r_start, r_end), (c_start, c_end)))
        # normalize
        data = np.array(
            data / self.tif_norm * self.jpg_norm).astype(int)
        return data
