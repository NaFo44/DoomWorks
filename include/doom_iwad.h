#ifndef DOOM_IWAD_H
#define DOOM_IWAD_H

#ifndef NUMWORKS
extern const unsigned char doom_iwad[];
extern const unsigned int doom_iwad_len;
#endif

const unsigned char* doom_iwad_data(void);
unsigned int doom_iwad_size(void);

#endif // DOOM_IWAD_H
