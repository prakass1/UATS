from time import time

import tensorflow as tf
from keras.backend.tensorflow_backend import set_session
from keras.callbacks import Callback
from keras.callbacks import ModelCheckpoint, TensorBoard, CSVLogger, EarlyStopping

from dataset_specific.prostate.generator import DataGenerator as train_gen
from dataset_specific.prostate.model import weighted_model
from old.utils.AugmentationGenerator import *
from old.utils.preprocess_images import get_complete_array, get_array, save_array
from utility.parallel_gpu_checkpoint import ModelCheckpointParallel

# 294 Training 58 have gt
learning_rate = 5e-5
TEMP = 1
# TEMP = 2.908655

FOLD_NUM = 2
TRAIN_NUM = 30
NAME = 'softmax2_F' + str(FOLD_NUM) + '_' + str(TRAIN_NUM) + '_' + str(TEMP)
TB_LOG_DIR = '/data/suhita/temporal/tb/' + NAME + '_' + str(learning_rate) + '/'
MODEL_NAME = '/data/suhita/temporal/' + NAME + '.h5'

CSV_NAME = '/data/suhita/temporal/CSV/' + NAME + '.csv'

TRAIN_IMGS_PATH = '/cache/suhita/data/fold' + str(FOLD_NUM) + '_' + str(TRAIN_NUM) + '/train/imgs/'
TRAIN_GT_PATH = '/cache/suhita/data/fold' + str(FOLD_NUM) + '_' + str(TRAIN_NUM) + '/train/gt/'

VAL_IMGS_PATH = '/cache/suhita/data/fold' + str(FOLD_NUM) + '_' + str(TRAIN_NUM) + '/val/imgs/'
VAL_GT_PATH = '/cache/suhita/data/fold' + str(FOLD_NUM) + '_' + str(TRAIN_NUM) + '/val/gt/'

TRAINED_MODEL_PATH = '/data/suhita/temporal/' + 'supervised_F' + str(FOLD_NUM) + '_' + str(TRAIN_NUM) + '.h5'
# TRAINED_MODEL_PATH = MODEL_NAME

ENS_GT_PATH = '/data/suhita/temporal/sadv2/ens_gt/'
FLAG_PATH = '/data/suhita/temporal/sadv2/flag/'

PERCENTAGE_OF_PIXELS = 5

NUM_CLASS = 5
num_epoch = 351
batch_size = 2
IMGS_PER_ENS_BATCH = 59  # 236/4 = 59

# hyper-params
SAVE_WTS_AFTR_EPOCH = 0
ramp_up_period = 50
ramp_down_period = 50
# weight_max = 40
# weight_max = 30

alpha = 0.6


def train(gpu_id, nb_gpus):
    num_labeled_train = TRAIN_NUM
    num_train_data = len(os.listdir(TRAIN_IMGS_PATH))
    num_un_labeled_train = num_train_data - num_labeled_train
    num_val_data = len(os.listdir(VAL_IMGS_PATH))

    # gen_lr_weight = ramp_down_weight(ramp_down_period)

    # prepare dataset
    print('-' * 30)
    print('Loading train data...')
    print('-' * 30)

    # Build Model
    wm = weighted_model()

    model = wm.build_model(num_class=NUM_CLASS, use_dice_cl=False, learning_rate=learning_rate, gpu_id=gpu_id,
                           nb_gpus=nb_gpus, trained_model=TRAINED_MODEL_PATH, temp=TEMP)

    print("Images Size:", num_train_data)
    print("Unlabeled Size:", num_un_labeled_train)

    print('-' * 30)
    print('Creating and compiling model...')
    print('-' * 30)

    model.summary()

    class TemporalCallback(Callback):

        def __init__(self, imgs_path, gt_path, ensemble_path, supervised_flag_path, train_idx_list):

            self.val_afs_dice_coef = 0.
            self.val_bg_dice_coef = 0.
            self.val_cz_dice_coef = 0.
            self.val_pz_dice_coef = 0.
            self.val_us_dice_coef = 0.
            self.count = TRAIN_NUM * 168 * 168 * 32

            self.imgs_path = imgs_path
            self.gt_path = gt_path
            self.ensemble_path = ensemble_path
            self.supervised_flag_path = supervised_flag_path
            self.train_idx_list = train_idx_list  # list of indexes of training eg
            self.confident_pixels_no = (PERCENTAGE_OF_PIXELS * 168 * 168 * 32 * num_un_labeled_train) // 100

            unsupervised_target = get_complete_array(TRAIN_GT_PATH, dtype='float32')
            flag = np.ones((32, 168, 168)).astype('float16')

            for patient in np.arange(num_train_data):
                np.save(self.ensemble_path + str(patient) + '.npy', unsupervised_target[patient])
                if patient < num_labeled_train:
                    np.save(self.supervised_flag_path + str(patient) + '.npy', flag)
                else:
                    np.save(self.supervised_flag_path + str(patient) + '.npy',
                            np.zeros((32, 168, 168)).astype('float32'))
            del unsupervised_target

        def on_batch_begin(self, batch, logs=None):
            pass

        def shall_save(self, cur_val, prev_val):
            flag_save = False
            val_save = prev_val

            if cur_val > prev_val:
                flag_save = True
                val_save = cur_val

            return flag_save, val_save

        def on_epoch_begin(self, epoch, logs=None):
            self.starttime = time()
            '''
            if epoch > num_epoch - ramp_down_period:
                weight_down = next(gen_lr_weight)
                K.set_value(model.optimizer.lr, weight_down * learning_rate)
                K.set_value(model.optimizer.beta_1, 0.4 * weight_down + 0.5)
                print('LR: alpha-', K.eval(model.optimizer.lr), K.eval(model.optimizer.beta_1))
            # print(K.eval(model.layers[43].trainable_weights[0]))
'''

        def on_epoch_end(self, epoch, logs={}):
            # print(time() - self.starttime)
            # model_temp = model

            sup_count = self.count
            pz_save, self.val_pz_dice_coef = self.shall_save(logs['val_pz_dice_coef'], self.val_pz_dice_coef)
            cz_save, self.val_cz_dice_coef = self.shall_save(logs['val_cz_dice_coef'], self.val_cz_dice_coef)
            us_save, self.val_us_dice_coef = self.shall_save(logs['val_us_dice_coef'], self.val_us_dice_coef)
            afs_save, self.val_afs_dice_coef = self.shall_save(logs['val_afs_dice_coef'], self.val_afs_dice_coef)
            bg_save, self.val_bg_dice_coef = self.shall_save(logs['val_bg_dice_coef'], self.val_bg_dice_coef)

            if epoch > 0:

                patients_per_batch = IMGS_PER_ENS_BATCH
                num_batches = num_un_labeled_train // patients_per_batch
                remainder = num_un_labeled_train % patients_per_batch
                num_batches = num_batches if remainder is 0 else num_batches + 1

                for b_no in np.arange(num_batches):
                    actual_batch_size = patients_per_batch if (
                            b_no <= num_batches - 1 and remainder == 0) else remainder
                    start = (b_no * patients_per_batch) + num_labeled_train
                    end = (start + actual_batch_size)
                    imgs = get_array(self.imgs_path, start, end)
                    ensemble_prediction = get_array(self.ensemble_path, start, end, dtype='float32')
                    supervised_flag = get_array(self.supervised_flag_path, start, end, dtype='float16')

                    inp = [imgs, ensemble_prediction, supervised_flag]
                    del imgs, supervised_flag

                    cur_pred = np.zeros((actual_batch_size, 32, 168, 168, NUM_CLASS))
                    # cur_sigmoid_pred = np.zeros((actual_batch_size, 32, 168, 168, NUM_CLASS))
                    model_out = model.predict(inp, batch_size=2, verbose=1)  # 1

                    # model_out = np.add(model_out, train.predict(inp, batch_size=2, verbose=1))  # 2
                    del inp

                    cur_pred[:, :, :, :, 0] = model_out[0] if pz_save else ensemble_prediction[:, :, :, :, 0]
                    cur_pred[:, :, :, :, 1] = model_out[1] if cz_save else ensemble_prediction[:, :, :, :, 1]
                    cur_pred[:, :, :, :, 2] = model_out[2] if us_save else ensemble_prediction[:, :, :, :, 2]
                    cur_pred[:, :, :, :, 3] = model_out[3] if afs_save else ensemble_prediction[:, :, :, :, 3]
                    cur_pred[:, :, :, :, 4] = model_out[4] if bg_save else ensemble_prediction[:, :, :, :, 4]

                    del model_out

                    # Z = αZ + (1 - α)z
                    ensemble_prediction = alpha * ensemble_prediction + (1 - alpha) * cur_pred
                    save_array(self.ensemble_path, ensemble_prediction, start, end)
                    del ensemble_prediction

                    # flag = np.where(np.reshape(np.max(ensemble_prediction, axis=-1),supervised_flag.shape) >= THRESHOLD, np.ones_like(supervised_flag),np.zeros_like(supervised_flag))
                    # dont consider background
                    # cur_pred[:, :, :, :, 4] = np.zeros((actual_batch_size, 32, 168, 168))
                    argmax_pred_ravel = np.ravel(np.argmax(cur_pred, axis=-1))
                    max_pred_ravel = np.ravel(np.max(cur_pred, axis=-1))
                    indices = None
                    del cur_pred
                    for zone in np.arange(5):
                        final_max_ravel = np.where(argmax_pred_ravel == zone, np.zeros_like(max_pred_ravel),
                                                   max_pred_ravel)
                        zone_indices = np.argpartition(final_max_ravel, -self.confident_pixels_no)[
                                       -self.confident_pixels_no:]
                        if zone == 0:
                            indices = zone_indices
                        else:
                            indices = np.unique(np.concatenate((zone_indices, indices)))

                    mask = np.ones(max_pred_ravel.shape, dtype=bool)
                    mask[indices] = False

                    max_pred_ravel[mask] = 0
                    max_pred_ravel = np.where(max_pred_ravel > 0, np.ones_like(max_pred_ravel) * 2,
                                              np.zeros_like(max_pred_ravel))
                    flag = np.reshape(max_pred_ravel, (actual_batch_size, 32, 168, 168))
                    # flag = np.reshape(max_pred_ravel, (IMGS_PER_ENS_BATCH, 32, 168, 168))
                    del max_pred_ravel, indices

                    save_array(self.supervised_flag_path, flag, start, end)

                    sup_count = sup_count + np.count_nonzero(flag)
                    del flag

                if 'cur_pred' in locals(): del cur_pred

    # callbacks
    print('-' * 30)
    print('Creating callbacks...')
    print('-' * 30)
    csv_logger = CSVLogger(CSV_NAME, append=True, separator=';')
    # model_checkpoint = ModelCheckpoint(MODEL_NAME, monitor='val_loss', save_best_only=True,verbose=1, mode='min')
    if nb_gpus is not None and nb_gpus > 1:
        model_checkpoint = ModelCheckpointParallel(MODEL_NAME,
                                                   monitor='val_loss',
                                                   save_best_only=True,
                                                   verbose=1,
                                                   mode='min')
    else:
        model_checkpoint = ModelCheckpoint(MODEL_NAME, monitor='val_loss',
                                           save_best_only=True,
                                           verbose=1,
                                           mode='min')

    tensorboard = TensorBoard(log_dir=TB_LOG_DIR, write_graph=False, write_grads=True, histogram_freq=0,
                              batch_size=2, write_images=False)

    # datagen listmake_dataset
    # t_list = get_train_id_list(FOLD_NUM)
    # train_id_list = [str(i) for i in t_list]
    train_id_list = [str(i) for i in np.arange(0, num_train_data)]

    tcb = TemporalCallback(TRAIN_IMGS_PATH, TRAIN_GT_PATH, ENS_GT_PATH, FLAG_PATH, train_id_list)
    lcb = wm.LossCallback()
    es = EarlyStopping(monitor='val_loss', mode='min', verbose=1, patience=50)
    # del unsupervised_target, unsupervised_weight, supervised_flag, imgs
    # del supervised_flag
    cb = [model_checkpoint, tcb, tensorboard, lcb, csv_logger, es]

    print('BATCH Size = ', batch_size)

    print('Callbacks: ', cb)
    # params = {'dim': (32, 168, 168),'batch_size': batch_size}

    print('-' * 30)
    print('Fitting train...')
    print('-' * 30)
    training_generator = train_gen(TRAIN_IMGS_PATH,
                                   TRAIN_GT_PATH,
                                   ENS_GT_PATH,
                                   FLAG_PATH,
                                   train_id_list,
                                   batch_size=batch_size,
                                   labelled_num=TRAIN_NUM)

    steps = num_train_data / batch_size
    # steps =2

    val_supervised_flag = np.ones((num_val_data, 32, 168, 168), dtype='int8') * 3
    # val_x_arr = get_complete_array(VAL_IMGS_PATH)
    # val_y_arr = get_complete_array(VAL_GT_PATH, dtype='int8')

    val_x_arr = get_complete_array(VAL_IMGS_PATH)
    val_y_arr = get_complete_array(VAL_GT_PATH, dtype='int8')

    pz = val_y_arr[:, :, :, :, 0]
    cz = val_y_arr[:, :, :, :, 1]
    us = val_y_arr[:, :, :, :, 2]
    afs = val_y_arr[:, :, :, :, 3]
    bg = val_y_arr[:, :, :, :, 4]

    y_val = [pz, cz, us, afs, bg]
    x_val = [val_x_arr, val_y_arr, val_supervised_flag]
    del val_supervised_flag, pz, cz, us, afs, bg, val_y_arr, val_x_arr

    history = model.fit_generator(generator=training_generator,
                                  steps_per_epoch=steps,
                                  validation_data=[x_val, y_val],
                                  epochs=num_epoch,
                                  callbacks=cb
                                  )

    # workers=4)
    # train.save('temporal_max_ramp_final.h5')


def predict(val_x_arr, val_y_arr, model):
    val_supervised_flag = np.ones((val_x_arr.shape[0], 32, 168, 168), dtype='int8')

    pz = val_y_arr[:, :, :, :, 0]
    cz = val_y_arr[:, :, :, :, 1]
    us = val_y_arr[:, :, :, :, 2]
    afs = val_y_arr[:, :, :, :, 3]
    bg = val_y_arr[:, :, :, :, 4]

    y_val = [pz, cz, us, afs, bg]
    x_val = [val_x_arr, val_y_arr, val_supervised_flag]
    wm = weighted_model()
    model = wm.build_model(num_class=NUM_CLASS, learning_rate=learning_rate, gpu_id=None,
                           nb_gpus=None, trained_model=model, temp=1)
    print('load_weights')
    # train.load_weights()
    print('predict')
    out = model.predict(x_val, batch_size=1, verbose=1)
    print(model.metrics_names)
    print(model.evaluate(x_val, y_val, batch_size=1, verbose=1))

    np.save('predicted_sl2' + '.npy', out)


if __name__ == '__main__':
    gpu = '/GPU:0'
    # gpu = '/GPU:0'
    batch_size = batch_size
    gpu_id = '3'

    # gpu_id = '0'
    # gpu = "GPU:0"  # gpu_id (default id is first of listed in parameters)
    # os.environ["CUDA_VISIBLE_DEVICES"] = '2'
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu_id
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    config = tf.compat.v1.ConfigProto()
    config.gpu_options.allow_growth = True
    config.allow_soft_placement = True
    set_session(tf.compat.v1.Session(config=config))

    nb_gpus = len(gpu_id.split(','))
    assert np.mod(batch_size, nb_gpus) == 0, \
        'batch_size should be a multiple of the nr. of gpus. ' + \
        'Got batch_size %d, %d gpus' % (batch_size, nb_gpus)

    # train(gpu, nb_gpus)
    # train(None, None)
    # val_x = np.load('/cache/suhita/data/validation/valArray_imgs_fold1.npy')
    # val_y = np.load('/cache/suhita/data/validation/valArray_GT_fold1.npy').astype('int8')

    val_x = np.load('/cache/suhita/data/final_test_array_imgs.npy')
    val_y = np.load('/cache/suhita/data/final_test_array_GT.npy').astype('int8')
    predict(val_x, val_y, model='/data/suhita/temporal/softmax2_F1_6_1.h5')
