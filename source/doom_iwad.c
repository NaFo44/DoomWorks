#pragma GCC optimize ("-O0")
#include "doom_iwad.h"

#ifdef NUMWORKS
#include <eadk.h>
#endif

#if defined(NUMWORKS)

#ifndef USE_EXTERNAL_IWAD
#define USE_EXTERNAL_IWAD 0
#endif

#if USE_EXTERNAL_IWAD
static int NumWorksExternalIwadAvailable(void)
{
	return eadk_external_data_size >= 4 &&
		   eadk_external_data[0] == 'I' &&
		   eadk_external_data[1] == 'W' &&
		   eadk_external_data[2] == 'A' &&
		   eadk_external_data[3] == 'D';
}
#endif

#endif

#if defined(NUMWORKS) && PLATFORM_DEVICE && USE_EXTERNAL_IWAD

const unsigned char* doom_iwad_data(void)
{
	if (NumWorksExternalIwadAvailable())
	{
		return (const unsigned char*)eadk_external_data;
	}

	return 0;
}

unsigned int doom_iwad_size(void)
{
	if (NumWorksExternalIwadAvailable())
	{
		return (unsigned int)eadk_external_data_size;
	}

	return 0;
}

#else

#ifndef EMBEDDED_IWAD_INCLUDE
YOU NEED TO SET THE WAD= TO A VALID WAD
#endif

#include EMBEDDED_IWAD_INCLUDE

const unsigned int doom_iwad_len = sizeof(doom_iwad);

const unsigned char* doom_iwad_data(void)
{
#if defined(NUMWORKS) && USE_EXTERNAL_IWAD
	if (NumWorksExternalIwadAvailable())
	{
		return (const unsigned char*)eadk_external_data;
	}
#endif

	return doom_iwad;
}

unsigned int doom_iwad_size(void)
{
#if defined(NUMWORKS) && USE_EXTERNAL_IWAD
	if (NumWorksExternalIwadAvailable())
	{
		return (unsigned int)eadk_external_data_size;
	}
#endif

	return doom_iwad_len;
}

#endif
